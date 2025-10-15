import logging
import netrc
import os
import ssl
import time
import urllib.request
from typing import Optional

import odoorpc

from ..core import SyncException, get_logger

logger = get_logger(__name__)


class OdooInstance:
    """Abstraction of an Odoo Instance."""

    def __init__(self, odoo_instance: dict, default_netrc_path: Optional[str] = None):
        opener = False
        protocol = "jsonrpc"

        if odoo_instance.get("ssl"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
            protocol = "jsonrpc+ssl"

        self.host = odoo_instance.get("host")
        self.port = odoo_instance.get("port")
        self.database = odoo_instance.get("database")
        self.netrc_path = self._select_netrc_path(odoo_instance.get("netrc_path"), default_netrc_path)

        self.odoo = odoorpc.ODOO(self.host, port=self.port, opener=opener, protocol=protocol)
        self._login()
        self.ir_model_obj = self.odoo.env["ir.model.data"]
        self._get_timestamp()

    def _select_netrc_path(self, instance_path: Optional[str], default_netrc_path: Optional[str]) -> Optional[str]:
        candidates = [
            instance_path,
            default_netrc_path,
            os.environ.get("ODOOSYNC_NETRC"),
            os.environ.get("NETRC"),
        ]
        for candidate in candidates:
            if candidate:
                return os.path.expanduser(candidate)
        return None

    def _login(self) -> None:
        try:
            if self.netrc_path:
                logger.debug("Loading credentials from netrc file %s", self.netrc_path)
                netrc_info = netrc.netrc(self.netrc_path)
            else:
                netrc_info = netrc.netrc()
        except (IOError, FileNotFoundError) as exc:
            logger.error("Failed to load netrc credentials for host %s: %s", self.host, exc)
            raise SyncException(self.host)
        auth_info = netrc_info.authenticators(self.host)
        if not auth_info:
            raise SyncException(self.host)
        username, _, password = auth_info
        logger.info("Connecting to host=%s, database=%s, user=%s", self.host, self.database, username)
        self.odoo.login(self.database, username, password)

    def _get_timestamp(self) -> None:
        obj = self.ir_model_obj
        dummy_record_id = obj.create(
            {
                "model": "res.users",
                "module": "__sfit_export_internals",
                "name": "__sfit_timestamp_{}".format(int(time.time())),
                "res_id": self.odoo.env.uid,
            }
        )
        dummy_record = obj.read([dummy_record_id])
        self.timestamp = dummy_record[0]["create_date"]
        obj.unlink(dummy_record_id)


__all__ = ["OdooInstance"]
