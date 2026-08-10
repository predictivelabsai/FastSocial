from __future__ import annotations

from fastsocial.models import ConnectionProvider, SocialAccount
from fastsocial.social.base import SocialClient
from fastsocial.social.direct import BlueskyClient, LinkedInClient, MockClient, XClient
from fastsocial.social.mcp import ManagedMCPClient


def client_for(account: SocialAccount) -> SocialClient:
    if account.provider == ConnectionProvider.mock:
        return MockClient()
    if account.provider in {ConnectionProvider.arcade, ConnectionProvider.composio}:
        return ManagedMCPClient(account.provider)
    clients = {"x": XClient, "linkedin": LinkedInClient, "bluesky": BlueskyClient}
    try:
        return clients[account.platform]()
    except KeyError as exc:
        raise ValueError(f"Unsupported direct platform: {account.platform}") from exc
