"""AWS IoT control plane — writing setpoints to the device shadow.

The CAS REST API this integration reads from has no write route. Control goes to
the AWS IoT device shadow over MQTT, authenticated with a per-install X.509
certificate. The flow (reverse-engineered from the official app):

  1. Cognito SRP sign-in as the app's shared service account.
  2. Cognito Identity federation -> temporary AWS credentials.
  3. iot:CreateKeysAndCertificate -> a client certificate, minted once.
  4. POST /aws/policy (unauthenticated) -> CAS attaches the shadow policy.
  5. MQTT publish to $aws/things/{thing}/shadow/update with a `desired` doc.

The certificate is minted once and cached in HA storage; subsequent writes reuse
it. All of this is blocking (boto3 / paho / requests), so callers run it in the
executor via the async wrappers.
"""
from __future__ import annotations

import json
import logging
import ssl
import time
from typing import Any

import boto3
import paho.mqtt.client as mqtt
import requests
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pycognito.aws_srp import AWSSRP

from .const import (
    ATTACH_POLICY_URL,
    AWS_REGION,
    COGNITO_CLIENT_ID,
    COGNITO_CLIENT_SECRET,
    COGNITO_IDENTITY_POOL_ID,
    COGNITO_USER_POOL_ID,
    DOMAIN,
    IOT_ATS_ENDPOINT,
    SVC_PASSWORD,
    SVC_USERNAME,
)

_LOGGER = logging.getLogger(__name__)

_AMAZON_ROOT_CA_URL = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
_STORAGE_VERSION = 1
_MQTT_TIMEOUT = 12


class MasterbuiltControlError(Exception):
    """Raised when a setpoint write cannot be completed."""


class MasterbuiltControl:
    """Owns the per-install certificate and performs shadow writes."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        # One cert/device-id per config entry, persisted so we don't churn certs.
        self._store: Store = Store(hass, _STORAGE_VERSION, f"{DOMAIN}.{entry_id}.iot")
        self._creds: dict[str, str] | None = None  # {cert, key, device_id, root_ca}

    async def async_set_grill_target(self, thing_name: str, value: int) -> None:
        await self._async_publish(
            thing_name, {"heat": {"t2": {"trgt": int(value)}}}
        )

    async def async_set_probe_target(
        self, thing_name: str, probe: int, value: int
    ) -> None:
        await self._async_publish(
            thing_name, {"probes": {f"p{probe}": {"trgt": int(value)}}}
        )

    async def _async_publish(self, thing_name: str, desired: dict[str, Any]) -> None:
        creds = await self._async_ensure_cert(thing_name)
        await self._hass.async_add_executor_job(
            self._publish, thing_name, desired, creds
        )

    # -- certificate provisioning ------------------------------------------

    async def _async_ensure_cert(self, thing_name: str) -> dict[str, str]:
        if self._creds is not None:
            return self._creds
        stored = await self._store.async_load()
        if stored and all(k in stored for k in ("cert", "key", "device_id", "root_ca")):
            self._creds = stored
            return stored
        creds = await self._hass.async_add_executor_job(self._provision, thing_name)
        await self._store.async_save(creds)
        self._creds = creds
        return creds

    def _provision(self, thing_name: str) -> dict[str, str]:
        """Mint a certificate and have CAS attach the shadow policy. Blocking."""
        device_id = f"ha-{thing_name[:12]}"
        try:
            aws = self._service_credentials()
            iot = boto3.client(
                "iot",
                region_name=AWS_REGION,
                aws_access_key_id=aws["AccessKeyId"],
                aws_secret_access_key=aws["SecretKey"],
                aws_session_token=aws["SessionToken"],
            )
            cert = iot.create_keys_and_certificate(setAsActive=True)
        except Exception as err:
            raise MasterbuiltControlError(f"certificate minting failed: {err}") from err

        try:
            resp = requests.post(
                ATTACH_POLICY_URL,
                json={
                    "aws_iot_certificate_arn": cert["certificateArn"],
                    "client_device_id": device_id,
                    "aws_iot_thing_name": thing_name,
                },
                timeout=20,
            )
            resp.raise_for_status()
        except requests.RequestException as err:
            raise MasterbuiltControlError(f"policy attach failed: {err}") from err

        root_ca = requests.get(_AMAZON_ROOT_CA_URL, timeout=20).text
        return {
            "cert": cert["certificatePem"],
            "key": cert["keyPair"]["PrivateKey"],
            "device_id": device_id,
            "root_ca": root_ca,
        }

    def _service_credentials(self) -> dict[str, str]:
        idp = boto3.client("cognito-idp", region_name=AWS_REGION)
        srp = AWSSRP(
            username=SVC_USERNAME,
            password=SVC_PASSWORD,
            pool_id=COGNITO_USER_POOL_ID,
            client_id=COGNITO_CLIENT_ID,
            client_secret=COGNITO_CLIENT_SECRET,
            client=idp,
        )
        id_token = srp.authenticate_user()["AuthenticationResult"]["IdToken"]
        provider = f"cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
        ci = boto3.client("cognito-identity", region_name=AWS_REGION)
        iid = ci.get_id(
            IdentityPoolId=COGNITO_IDENTITY_POOL_ID, Logins={provider: id_token}
        )["IdentityId"]
        return ci.get_credentials_for_identity(
            IdentityId=iid, Logins={provider: id_token}
        )["Credentials"]

    # -- MQTT publish -------------------------------------------------------

    def _publish(
        self, thing_name: str, desired: dict[str, Any], creds: dict[str, str]
    ) -> None:
        """Connect over mTLS, publish one shadow update, wait for ack. Blocking.

        The MQTT client id MUST equal the device_id registered with the policy —
        iot:Connect is scoped to client/${device_id}; a mismatch drops the socket
        right after TLS.
        """
        import os
        import tempfile

        base = f"$aws/things/{thing_name}/shadow"
        result: dict[str, Any] = {}
        # paho needs file paths for the cert material.
        with tempfile.TemporaryDirectory() as tmp:
            cpath = os.path.join(tmp, "cert.pem")
            kpath = os.path.join(tmp, "key.pem")
            capath = os.path.join(tmp, "ca.pem")
            for path, data in ((cpath, creds["cert"]), (kpath, creds["key"]), (capath, creds["root_ca"])):
                with open(path, "w") as fh:
                    fh.write(data)

            client = mqtt.Client(client_id=creds["device_id"], protocol=mqtt.MQTTv311)
            client.tls_set(
                ca_certs=capath, certfile=cpath, keyfile=kpath,
                tls_version=ssl.PROTOCOL_TLSv1_2,
            )

            def on_connect(c, u, flags, rc):
                if rc != 0:
                    result["error"] = f"MQTT connect refused (rc={rc})"
                    return
                c.subscribe(f"{base}/update/accepted")
                c.subscribe(f"{base}/update/rejected")
                c.publish(f"{base}/update", json.dumps({"state": {"desired": desired}}), qos=1)

            def on_message(c, u, msg):
                result["topic"] = msg.topic
                result["done"] = True

            client.on_connect = on_connect
            client.on_message = on_message
            try:
                client.connect(IOT_ATS_ENDPOINT, 8883, keepalive=30)
            except Exception as err:
                raise MasterbuiltControlError(f"MQTT connect failed: {err}") from err

            client.loop_start()
            deadline = time.time() + _MQTT_TIMEOUT
            while time.time() < deadline and not result.get("done") and "error" not in result:
                time.sleep(0.2)
            client.loop_stop()
            client.disconnect()

        if "error" in result:
            raise MasterbuiltControlError(result["error"])
        if not result.get("done"):
            raise MasterbuiltControlError("no shadow ack within timeout")
        if result.get("topic", "").endswith("/rejected"):
            raise MasterbuiltControlError("shadow update rejected")
        _LOGGER.debug("shadow update accepted for %s: %s", thing_name, desired)
