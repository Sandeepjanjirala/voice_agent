"""Candidate SIP providers and what is actually known about each.

Nothing here is a default that lets a call through. A capability a plan depends
on stays a blocker until the owner records a confirmation for it in
TELEPHONY_CONFIRMED. The notes say what the vendor documents and what is still
unproven; see docs/history/PHASE2_READINESS.md for sources.
"""

from dataclasses import dataclass, field

# Confirmation tokens the owner may list in TELEPHONY_CONFIRMED.
ACCOUNT_READY = "provider_account_ready"
REGIONAL_ENDPOINT = "livekit_regional_sip_endpoint"
INBOUND_DIGEST_AUTH = "provider_inbound_digest_auth"
ALLOWED_ADDRESSES_ENFORCED = "livekit_allowed_addresses_enforced"
INBOUND_SRTP = "provider_inbound_srtp"
OUTBOUND_READY = "provider_outbound_ready"
OUTBOUND_PLAIN_SIGNALLING = "outbound_plain_signalling_accepted"

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
    OUTBOUND_READY: (
        "The provider account passed KYC, is permitted to call Indian phone "
        "numbers, and owns the number in SIP_OUTBOUND_CALLER_ID."
    ),
    OUTBOUND_PLAIN_SIGNALLING: (
        "Add this only if the provider has said in writing that it has no TLS "
        "for outbound trunks and you accept unencrypted signalling for the test."
    ),
}


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    # Domestic Indian calls must keep signalling and media in India.
    required_sip_region: str | None
    # How the provider can prove an INVITE is its own, as far as its docs say.
    default_auth_mode: str = "digest"
    # Addresses the provider documents as the source of calls it delivers.
    # Shown as a hint only; never used unless copied into the environment.
    documented_source_addresses: tuple[str, ...] = ()
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
        default_auth_mode="ip_allowlist",
        documented_source_addresses=("13.233.44.61", "3.111.255.163", "3.111.128.110"),
        notes={
            ACCOUNT_READY: (
                "Individuals verify with Aadhaar e-KYC and PAN; no GST is asked "
                "for. Trial numbers are outbound only, so inbound needs KYC and a "
                "paid number. 140, 160 and 92-series numbers need extra approval."
            ),
            REGIONAL_ENDPOINT: (
                "LiveKit confirmed on 16 Nov 2026 that region-based telephony "
                "endpoints work on every plan, Build included, with no project "
                "enablement (community.livekit.io/t/2530). Vobiz says domestic "
                "media must stay in India; its guide uses the global endpoint."
            ),
            INBOUND_DIGEST_AUTH: (
                "Vobiz's documented origination URI has no username or password "
                "field. Inbound digest support is unconfirmed; use ip_allowlist "
                "unless Vobiz confirms another supported authentication method."
            ),
            ALLOWED_ADDRESSES_ENFORCED: (
                "Vobiz documents three source addresses for delivered calls. "
                "LiveKit said on 16 Nov 2026 it can enable enforcement for a "
                "Build project on request (community.livekit.io/t/2530). List "
                "this only once LiveKit says it is on for this project. Verify "
                "unlisted-source rejection against the configured trunk before "
                "accepting public traffic; an empty trunk list proves nothing."
            ),
            INBOUND_SRTP: (
                "Vobiz trunks have a 'secure' setting for SRTP and a TLS transport "
                "option in the console. Untested against LiveKit."
            ),
        },
    ),
}
