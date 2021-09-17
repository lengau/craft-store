# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright 2021 Canonical Ltd.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License version 3 as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Craft Store StoreClient."""

import base64
import json
from typing import TYPE_CHECKING, Any, Dict, Sequence
from urllib.parse import urlparse

import macaroonbakery._utils as utils
import requests
from macaroonbakery import bakery, httpbakery

from .auth import Auth
from .http_client import HTTPClient

if TYPE_CHECKING:
    from . import endpoints


class StoreClient(HTTPClient):
    """Encapsulates API calls for the Snap Store or Charmhub."""

    def __init__(
        self,
        *,
        base_url: str,
        endpoints: "endpoints.Endpoints",
        application_name: str,
        user_agent: str,
    ) -> None:
        """Initialize the Store Client.

        :param base_url: the base url of the API endpoint.
        :param endpoints: :data:`endpoints.CHARMHUB` or :data:`endpoints.SNAP_STORE`.
        :param application_name: the name application using this class, used for the keyring.
        :param user_agent: User-Agent header to use for HTTP(s) requests.
        """
        super().__init__(user_agent=user_agent)

        self._bakery_client = httpbakery.Client()
        self._base_url = base_url
        self._store_host = urlparse(base_url).netloc
        self._endpoints = endpoints
        self._auth = Auth(application_name, base_url)

    def _get_token(self, token_request: Dict[str, Any]) -> str:
        token_response = super().request(
            "POST",
            self._base_url + self._endpoints.tokens,
            json=token_request,
        )

        return token_response.json()["macaroon"]

    def _candid_discharge(self, macaroon: str) -> str:
        bakery_macaroon = bakery.Macaroon.from_dict(json.loads(macaroon))
        discharges = bakery.discharge_all(
            bakery_macaroon, self._bakery_client.acquire_discharge
        )

        # serialize macaroons the bakery-way
        discharged_macaroons = (
            "[" + ",".join(map(utils.macaroon_to_json_string, discharges)) + "]"
        )

        return base64.urlsafe_b64encode(utils.to_bytes(discharged_macaroons)).decode(
            "ascii"
        )

    def _authorize_token(self, candid_discharged_macaroon: str) -> str:
        token_exchange_response = super().request(
            "POST",
            self._base_url + self._endpoints.tokens_exchange,
            headers={"Macaroons": candid_discharged_macaroon},
        )

        return token_exchange_response.json()["macaroon"]

    def login(
        self,
        *,
        permissions: Sequence[str],
        description: str,
        ttl: str,
    ) -> None:
        """Obtain credentials to perform authenticated requests.

        Credentials are stored on the systems keyring, handled by
        :data:`craft_store.auth.Auth`.

        The list of permissions to select from can be referred to on
        :data:`craft_store.attenuations`.

        :param permissions: Set of permissions to grant the login.
        :param description: Client description to refer to from the Store.
        :param ttl: time to live for the credential, in other words, how
                    long until it expires, expressed in seconds.
        """
        token_request = self._endpoints.get_token_request(
            permissions=permissions, description=description, ttl=ttl
        )

        token = self._get_token(token_request)
        candid_discharged_macaroon = self._candid_discharge(token)
        store_authorized_macaroon = self._authorize_token(candid_discharged_macaroon)

        # Save the authorization token.
        self._auth.set_credentials(store_authorized_macaroon)

    def request(
        self,
        method: str,
        url: str,
        params: Dict[str, str] = None,
        headers: Dict[str, str] = None,
        **kwargs,
    ) -> requests.Response:
        """Perform an authenticated request if auth_headers are True.

        :param method: HTTP method used for the request.
        :param url: URL to request with method.
        :param params: Query parameters to be sent along with the request.
        :param headers: Headers to be sent along with the request.

        :raises errors.StoreServerError: for error responses.
        :raises errors.NetworkError: for lower level network issues.
        :raises errors.NotLoggedIn: if not logged in.

        :return: Response from the request.
        """
        if headers is None:
            headers = {}

        auth = self._auth.get_credentials()
        headers["Authorization"] = f"Macaroon {auth}"

        return super().request(
            method,
            url,
            params=params,
            headers=headers,
            **kwargs,
        )

    def whoami(self) -> Dict[str, Any]:
        """Return whoami json data queyring :data:`.endpoints.Endpoint.whoami`."""
        return self.request("GET", self._base_url + self._endpoints.whoami).json()

    def logout(self) -> None:
        """Clear credentials.

        :raises errors.NotLoggedIn: if not logged in.
        """
        self._auth.del_credentials()
