"""Candidate SIP providers and what is actually known about each.

Nothing here is a default that lets a call through. A capability a plan depends
on stays a blocker until the owner records a confirmation for it in
TELEPHONY_CONFIRMED. The notes say what the vendor documents and what is still
unproven; see docs/PHASE2_READINESS.md for sources.
"""

from dataclasses import dataclass, field

# Confirmation tokens the owner may list in TELEPHONY_CONFIRMED.
ACCOUNT_READY = "provider_account_ready"
REGIONAL_ENDPOINT = "livekit_regional_sip_endpoint"
INBOUND_DIGEST_AUTH = "provider_inbound_digest_auth"
ALLOWED_ADDRESSES_ENFORCED = "livekit_allowed_addresses_enforced"
INBOUND_SRTP = "provider_inbound_srtp"

CONFIRMATIONS: dict[str, str] = {
    ACCOUNT_READY: (
        "The provider account passed KYC and owns the inbound number in "
        "TELEPHONY_INBOUND_NUMBER."
    ),
    REGIONAL_ENDPOINT: (
        "LiveKit confirmed in writing that the regional SIP endpoint works on this "
        "project's plan."
    ),
    INBOUND_DIGEST_AUTH: (
        "The provider confirmed it answers LiveKit's digest challenge on inbound "
        "INVITEs with the configured username and password."
    ),
    ALLOWED_ADDRESSES_ENFORCED: (
        "LiveKit support confirmed allowed_addresses is enforced for this project. "
        "Until then LiveKit saves the list but accepts calls from any address."
    ),
    INBOUND_SRTP: (
        "The provider confirmed SRTP media encryption on inbound calls to an "
        "external SIP address."
    ),
}


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    # Domestic Indian calls must keep signalling and media in India.
    required_sip_region: str | None
    notes: dict[str, str] = field(default_factory=dict)

    def note(self, confirmation: str) -> str:
        return self.notes.get(confirmation, "Not documented by the provider.")


PROVIDERS: dict[str, Provider] = {
    "plivo": Provider(
        key="plivo",
        label="Plivo (India region)",
        required_sip_region="india",
        notes={
            ACCOUNT_READY: (
                "Needs an India-region organisation and business KYC. Eligibility "
                "of this account is unverified."
            ),
            REGIONAL_ENDPOINT: (
                "LiveKit's Plivo guide says India calls fail without the regional "
                "endpoint. Whether it works on the Build plan is unconfirmed."
            ),
            INBOUND_DIGEST_AUTH: (
                "Plivo's origination URI has authentication_needed, username and "
                "password fields. The mechanism is not named and it is untested "
                "against LiveKit."
            ),
            INBOUND_SRTP: (
                "Plivo lists secure trunking at no charge; LiveKit documents the "
                "switch for outbound trunks only."
            ),
        },
    ),
    "vobiz": Provider(
        key="vobiz",
        label="Vobiz",
        required_sip_region="india",
        notes={
            ACCOUNT_READY: (
                "Trial numbers are outbound only; inbound needs KYC and a paid "
                "number. India prices are not published."
            ),
            REGIONAL_ENDPOINT: (
                "Vobiz says domestic media must stay in India. Its LiveKit guide "
                "uses the global endpoint, so the regional one is untested."
            ),
            INBOUND_DIGEST_AUTH: (
                "Vobiz trunks support digest credentials; whether they are sent on "
                "inbound calls to an external address is not documented. Its own "
                "example opens the trunk to 0.0.0.0/0."
            ),
            INBOUND_SRTP: "Not published by Vobiz.",
        },
    ),
}
