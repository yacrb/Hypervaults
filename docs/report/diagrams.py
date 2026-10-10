"""Editable SVG diagrams using local brand assets and a consistent purple palette."""

from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

PURPLE, INK, MUTED = "#7654C4", "#231B36", "#70677D"
ICONS = {
    "user": '<circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3"/>',
    "lock": '<rect x="4" y="10" width="16" height="12" rx="3"/><path d="M7 10V7a5 5 0 0 1 10 0v3M12 15v3"/>',
    "shield": '<path d="M12 2 3 6v6c0 6 9 10 9 10s9-4 9-10V6Z"/><path d="m8 12 3 3 5-6"/>',
    "wallet": '<rect x="2" y="5" width="20" height="16" rx="3"/><path d="M3 5V3h15M16 11h6v6h-6Z"/>',
    "server": '<rect x="2" y="3" width="20" height="7" rx="2"/><rect x="2" y="14" width="20" height="7" rx="2"/><path d="M6 6h1M6 17h1M11 6h7M11 17h7"/>',
    "key": '<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9M16 16l3-3M18 18l3-3"/>',
    "check": '<circle cx="12" cy="12" r="10"/><path d="m6 12 4 4 8-9"/>',
    "code": '<path d="m8 5-6 7 6 7M16 5l6 7-6 7M14 2l-4 20"/>',
    "qr": '<path d="M2 2h7v7H2ZM15 2h7v7h-7ZM2 15h7v7H2ZM15 15h3v3h-3ZM21 14v8h-7M11 2v10H2M12 16v6M17 11h5"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 3"/>',
}


class Diagram:
    def __init__(self, title, subtitle, height=740):
        self.height, self.parts = height, []
        self.parts.append('<rect width="1200" height="100%" rx="26" fill="#FBFAFF"/>')
        self.text(36, 43, title, 27, INK, 700)
        self.text(36, 73, subtitle, 18)
        self.edges, self.nodes = [], []

    def text(self, x, y, text, size=19, color=MUTED, weight=400):
        self.parts.append(
            f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}">{escape(text)}</text>'
        )

    def zone(self, x, y, w, h, title):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="#F1EDF8" stroke="#DCD2EE"/>'
        )
        self.text(x + 20, y + 33, title, 20, PURPLE, 700)

    def node(self, x, y, w, h, title, lines=(), icon="shield", tint=False):
        self.nodes.append(
            f'<g data-card="true" data-x="{x}" data-y="{y}" data-width="{w}" data-height="{h}">'
        )
        self.nodes.append(
            f'<rect x="{x}" y="{y + 4}" width="{w}" height="{h}" rx="16" fill="#DDD4ED" opacity=".45"/>'
        )
        self.nodes.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{"#ECE5FB" if tint else "white"}" stroke="#D8CBEA"/>'
        )
        if icon in ICONS:
            shape = f'<g fill="none" stroke="{PURPLE}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{ICONS[icon]}</g>'
        else:
            root = ET.parse(Path(__file__).parent / "logos" / f"{icon}.svg").getroot()
            shape = "".join(
                ET.tostring(e, encoding="unicode")
                for e in root
                if e.tag.endswith("path")
            )
            shape = f'<g fill="{PURPLE}">{shape}</g>'
        self.nodes.append(
            f'<g transform="translate({x + 18},{y + 17}) scale(1.35)">{shape}</g>'
        )
        title_size = min(22, (w - 84) / (len(title) * 0.54))
        self.nodes.append(
            f'<text x="{x + 65}" y="{y + 41}" fill="{INK}" font-size="{title_size:.1f}" font-weight="700">{escape(title)}</text>'
        )
        for i, line in enumerate(lines):
            self.nodes.append(
                f'<text x="{x + 19}" y="{y + 68 + i * 24}" fill="{MUTED}" font-size="18">{escape(line)}</text>'
            )
        self.nodes.append("</g>")

    def arrow(self, points, label=None, label_at=None, dashed=False):
        path = "M " + " L ".join(f"{x},{y}" for x, y in points)
        self.edges.append(
            f'<path d="{path}" fill="none" stroke="{PURPLE}" stroke-width="2.8" stroke-linejoin="round" marker-end="url(#arrow)" {'stroke-dasharray="7 6"' if dashed else ""}/>'
        )
        if label:
            x, y = label_at or points[len(points) // 2]
            self.edges.append(
                f'<rect x="{x - 7}" y="{y - 21}" width="{len(label) * 9 + 16}" height="29" rx="7" fill="#FBFAFF"/><text x="{x}" y="{y}" fill="{PURPLE}" font-size="17" font-weight="600">{escape(label)}</text>'
            )

    def save(self, path):
        definitions = f'<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 8 4 0 8Z" fill="{PURPLE}"/></marker></defs>'
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{self.height}" viewBox="0 0 1200 {self.height}" style="font-family:Arial,sans-serif">{definitions}{"".join(self.parts + self.edges + self.nodes)}</svg>'
        path.write_text(svg)


def build(folder):
    folder.mkdir(parents=True, exist_ok=True)
    d = Diagram(
        "Architecture and trust boundaries",
        "Website identity, private records and wallet authority remain separate.",
        770,
    )
    d.zone(25, 102, 345, 575, "Browser and wallet")
    d.zone(410, 102, 340, 575, "Python application")
    d.zone(790, 102, 385, 575, "Data and network")
    d.node(
        45,
        153,
        305,
        100,
        "Sign-in",
        ["Email / password or SIWE", "Optional second-factor gate"],
        "user",
    )
    d.node(
        45,
        276,
        305,
        100,
        "Account security",
        ["MFA, wallets and passwords", "Sessions and security activity"],
        "shield",
    )
    d.node(
        45,
        400,
        305,
        100,
        "Dashboard",
        ["Overview, vaults, positions", "Role-limited administration"],
        "code",
    )
    d.node(
        45,
        535,
        305,
        113,
        "Browser wallet",
        ["Signs proofs and transactions", "Private keys stay in the wallet"],
        "wallet",
        True,
    )
    d.node(
        430,
        165,
        300,
        126,
        "FastAPI authentication",
        [
            "Argon2id / SIWE / MFA",
            "Session + Origin + CSRF",
            "Current account permissions",
        ],
        "fastapi",
    )
    d.node(
        430,
        335,
        300,
        113,
        "Authorized app API",
        ["Owner checks + private notes", "Canonical RPC and receipt reads"],
        "server",
    )
    d.node(
        430,
        515,
        300,
        112,
        "External key ring",
        ["AES-256-GCM keys", "Stored outside MongoDB"],
        "key",
    )
    d.node(
        815,
        175,
        335,
        180,
        "MongoDB",
        [
            "Users and hashed sessions",
            "Encrypted MFA secrets / notes",
            "Hashed one-use recovery codes",
            "Audit events and chain index",
        ],
        "mongodb",
    )
    d.node(
        815,
        482,
        335,
        151,
        "Sepolia contracts",
        [
            "HVT token + non-upgradeable vault",
            "Owner / maturity / reserve checks",
            "Wallet signs; API verifies",
        ],
        "ethereum",
        True,
    )
    d.arrow([(350, 203), (430, 203)])
    d.arrow([(350, 325), (385, 325), (385, 245), (430, 245)])
    d.arrow([(350, 450), (392, 450), (392, 392), (430, 392)])
    d.arrow([(730, 220), (815, 220)])
    d.arrow([(730, 375), (770, 375), (770, 302), (815, 302)])
    d.arrow([(580, 515), (580, 448)], "Encrypt", (592, 489))
    d.arrow([(730, 408), (772, 408), (772, 523), (815, 523)], "Read RPC", (768, 445))
    d.arrow(
        [(210, 648), (210, 700), (978, 700), (978, 633)],
        "Signed transactions",
        (440, 699),
    )
    d.text(
        36,
        747,
        "Runtime: Docker app + MongoDB on a private bridge; host bindings are loopback-only.",
        18,
    )
    d.save(folder / "architecture.svg")

    d = Diagram(
        "One sign-in gate for both methods",
        "A correct first factor alone grants no access when MFA is enabled.",
        700,
    )
    d.node(
        35,
        126,
        270,
        112,
        "Email sign-in",
        ["Validate input and rate limit", "Verify Argon2id password"],
        "user",
    )
    d.node(
        35,
        282,
        270,
        126,
        "Wallet sign-in",
        [
            "Verify signed SIWE challenge",
            "Domain, chain, nonce, expiry",
            "Consume the proof once",
        ],
        "wallet",
    )
    d.node(
        360,
        192,
        250,
        126,
        "Primary proof",
        [
            "Resolve the account",
            "Refuse a disabled identity",
            "Read current auth_version",
        ],
        "shield",
    )
    d.node(655, 190, 235, 99, "MFA enabled?", ["Server decides"], "lock", True)
    d.node(
        930,
        115,
        235,
        126,
        "Full session",
        ["Fresh opaque cookie", "Fresh CSRF token", "Account access unlocked"],
        "check",
        True,
    )
    d.node(
        655,
        360,
        235,
        127,
        "Pending session",
        ["Anonymous; no access", "Browser + user version", "Five-minute expiry"],
        "clock",
    )
    d.node(
        930,
        360,
        235,
        127,
        "Second factor",
        ["TOTP or recovery code", "Consume factor once", "Refuse invalid codes"],
        "qr",
    )
    d.node(
        660,
        560,
        505,
        90,
        "Protected API access",
        ["Check status, role and session lifetime on every request."],
        "shield",
    )
    d.arrow([(305, 185), (330, 185), (330, 234), (360, 234)])
    d.arrow([(305, 338), (330, 338), (330, 275), (360, 275)])
    d.arrow([(610, 245), (655, 245)])
    d.arrow([(890, 218), (913, 218), (913, 180), (930, 180)], "No", (899, 150))
    d.arrow([(773, 289), (773, 360)], "Yes", (784, 330))
    d.arrow([(890, 420), (930, 420)])
    d.arrow([(1045, 360), (1045, 241)], "Valid", (1057, 302))
    d.arrow([(1165, 175), (1183, 175), (1183, 606), (1165, 606)])
    d.save(folder / "sign-in.svg")

    d = Diagram(
        "Session lifecycle and revocation",
        "MFA-enabled sign-in rotates identifiers twice; the browser never stores a JWT.",
        610,
    )
    for x, title, lines, icon in [
        (35, "Anonymous", ["Bootstrap cookie + CSRF", "No user permissions"], "user"),
        (325, "Primary verified", ["Rotate to pending MFA", "Still anonymous"], "lock"),
        (
            615,
            "Factor verified",
            ["Consume factor atomically", "Rotate cookie + CSRF"],
            "qr",
        ),
        (
            905,
            "Authenticated",
            ["Hash stored server-side", "HttpOnly browser cookie"],
            "check",
        ),
    ]:
        d.node(x, 130, 255, 126, title, lines, icon, x == 905)
    for x in (290, 580, 870):
        d.arrow([(x, 194), (x + 35, 194)])
    d.node(
        40,
        350,
        520,
        170,
        "Each request resolves live account state",
        [
            "Reject expired idle or absolute lifetimes",
            "Reject disabled / missing accounts",
            "Reject auth_version mismatch",
            "Evaluate the current role for admin APIs",
        ],
        "server",
    )
    d.node(
        650,
        350,
        510,
        170,
        "Sensitive account changes",
        [
            "Password, MFA, linkage or admin updates",
            "Increment the authorization version",
            "Revoke affected sessions and tokens",
            "Keep only the authorized browser when applicable",
        ],
        "shield",
        True,
    )
    d.arrow([(1030, 256), (1030, 303), (300, 303), (300, 350)])
    d.arrow([(650, 450), (560, 450)], "Revoke", (570, 422))
    d.text(
        42,
        565,
        "Defaults: 30-minute idle lifetime / 8-hour absolute lifetime / 5-minute recent identity proof.",
        19,
    )
    d.save(folder / "sessions.svg")

    d = Diagram(
        "Connect then link then use",
        "Connecting exposes an address; only an ownership signature binds it to an account.",
        570,
    )
    d.node(
        35,
        132,
        335,
        170,
        "1  Connect",
        [
            "Discover available injected wallets",
            "Select the configured network",
            "Read the selected public address",
            "No website identity change",
        ],
        "wallet",
    )
    d.node(
        430,
        132,
        335,
        170,
        "2  Sign a link proof",
        [
            "Require recent website identity",
            "Issue a five-minute SIWE message",
            "Bind action, browser and account",
            "No token transfer is authorized",
        ],
        "ethereum",
    )
    d.node(
        825,
        132,
        335,
        170,
        "3  Link on the server",
        [
            "Verify signer and exact message",
            "Consume the challenge once",
            "Enforce a unique wallet owner",
            "Rotate access after linkage",
        ],
        "shield",
        True,
    )
    d.arrow([(370, 210), (430, 210)])
    d.arrow([(765, 210), (825, 210)])
    d.node(
        40,
        398,
        515,
        118,
        "Email-first account",
        [
            "Email for access; linked wallet for transactions.",
            "Both sign-in methods share one account.",
        ],
        "user",
    )
    d.node(
        645,
        398,
        515,
        118,
        "Wallet-first account",
        [
            "Recent wallet proof authorizes adding email + password.",
            "Enabled MFA also applies to that identity confirmation.",
        ],
        "lock",
    )
    d.text(
        40,
        352,
        "SIWE binds domain + URI + chain + nonce + action + time window + selected wallet.",
        20,
        PURPLE,
        700,
    )
    d.save(folder / "wallet-linking.svg")

    d = Diagram(
        "MFA enrollment is a verified transition",
        "A displayed QR never enables MFA by itself.",
        540,
    )
    d.node(
        35,
        123,
        335,
        150,
        "Confirm identity",
        [
            "Password or linked-wallet proof",
            "Recent identity window: 5 minutes",
            "Setup bound to browser and account",
        ],
        "user",
    )
    d.node(
        430,
        123,
        335,
        150,
        "Create enrollment",
        [
            "Generate a random 160-bit secret",
            "Encrypt it with purpose-bound GCM",
            "Generate QR locally from otpauth URI",
        ],
        "qr",
    )
    d.node(
        825,
        123,
        335,
        150,
        "Scan and verify",
        [
            "Authenticator imports the QR",
            "User supplies a six-digit TOTP",
            "Only a valid code enables MFA",
        ],
        "check",
        True,
    )
    d.arrow([(370, 200), (430, 200)])
    d.arrow([(765, 200), (825, 200)])
    d.node(
        40,
        365,
        515,
        124,
        "Protect authenticator state",
        [
            "TOTP: HMAC-SHA1, six digits, 30-second steps",
            "Accepted time steps are atomically one-use.",
            "Secret remains encrypted in the account record.",
        ],
        "lock",
    )
    d.node(
        645,
        365,
        515,
        124,
        "Save eight recovery codes",
        [
            "Each code contains 128 random bits",
            "Only SHA-256 hashes are stored",
            "Atomic removal makes each code single-use.",
        ],
        "key",
    )
    d.arrow([(997, 273), (997, 365)])
    d.arrow([(997, 315), (298, 315), (298, 365)])
    d.save(folder / "mfa-enrollment.svg")

    d = Diagram(
        "Encryption with authenticated context",
        "A valid key is not enough: the account, record and purpose must also match.",
        630,
    )
    d.node(
        390,
        115,
        360,
        100,
        "External AES key ring",
        ["32-byte keys and an active key ID", "Retain old keys while records use them"],
        "key",
        True,
    )
    d.node(
        35,
        260,
        290,
        124,
        "Authorized plaintext",
        [
            "Private note or MFA secret",
            "Owner access checked first",
            "Fresh 12-byte nonce reserved",
        ],
        "lock",
    )
    d.node(
        420,
        275,
        300,
        112,
        "AES-256-GCM",
        ["Encrypt and authenticate", "Include associated data"],
        "shield",
        True,
    )
    d.node(
        825,
        255,
        340,
        143,
        "MongoDB ciphertext",
        [
            "Key ID + nonce + ciphertext/tag",
            "No plaintext note or MFA key",
            "Shared nonce reservation index",
        ],
        "mongodb",
    )
    d.arrow([(325, 323), (420, 323)])
    d.arrow([(720, 323), (825, 323)])
    d.arrow([(570, 215), (570, 275)])
    d.node(
        40,
        482,
        520,
        108,
        "Associated data",
        [
            "purpose + user ID + deployment/position or enrollment",
            "Swapping account, record or purpose invalidates the tag.",
        ],
        "code",
    )
    d.node(
        650,
        482,
        510,
        108,
        "Read and verify",
        [
            "Recheck account/linkage after chain reads",
            "Invalid tag or context: refuse decryption (409).",
        ],
        "check",
    )
    d.arrow([(300, 482), (300, 429), (520, 429), (520, 387)], dashed=True)
    d.arrow([(999, 398), (999, 482)])
    d.save(folder / "encryption.svg")

    d = Diagram(
        "Authorization has three independent layers",
        "A website administrator cannot seize a user's on-chain position.",
        490,
    )
    d.node(
        35,
        124,
        345,
        238,
        "Website member",
        [
            "Manage own account and sessions",
            "Read only linked-wallet positions",
            "Encrypt/decrypt owned notes",
            "Admin APIs return 403",
            "Pending MFA returns 401",
        ],
        "user",
    )
    d.node(
        430,
        124,
        345,
        238,
        "Website administrator",
        [
            "Manage website roles and status",
            "Revoke affected sessions",
            "Admin APIs check current role",
            "Cannot disable/demote itself",
            "Has no user's wallet key",
        ],
        "shield",
        True,
    )
    d.node(
        825,
        124,
        340,
        238,
        "Contract wallet authority",
        [
            "Owner may change future offers",
            "Owner may pause new deposits",
            "Position owner alone withdraws",
            "Maturity is enforced on-chain",
            "No sweep / seizure function",
        ],
        "ethereum",
    )
    d.text(
        40,
        425,
        "UI visibility helps navigation; server dependencies and Solidity checks enforce permissions.",
        20,
        PURPLE,
        700,
    )
    d.save(folder / "authorization.svg")

    d = Diagram(
        "A transaction hash is not success",
        "The wallet sends the transaction; the API verifies its canonical result.",
        590,
    )
    stages = [
        (
            35,
            "Review",
            [
                "Exact network and contracts",
                "Terms and amount",
                "Exact token allowance",
            ],
            "wallet",
        ),
        (
            325,
            "Wallet signs",
            [
                "Check selected account",
                "Match chain/code fingerprint",
                "Store pending hash in tab",
            ],
            "ethereum",
        ),
        (
            615,
            "Verify receipt",
            [
                "Success + canonical block",
                "Required confirmations",
                "Expected signer and target",
            ],
            "server",
        ),
        (
            905,
            "Verify event",
            [
                "Expected contract event",
                "Correct owner / recipient",
                "Refresh confirmed state",
            ],
            "check",
        ),
    ]
    for x, title, lines, icon in stages:
        d.node(x, 133, 255, 160, title, lines, icon, x == 905)
    for x in (290, 580, 870):
        d.arrow([(x, 213), (x + 35, 213)])
    d.node(
        40,
        395,
        520,
        133,
        "Deposit",
        [
            "Reward reserve must cover the entire fixed reward",
            "Expected duration / rate reject changed offer terms",
            "Principal and reward become recorded liabilities",
        ],
        "lock",
    )
    d.node(
        650,
        395,
        510,
        133,
        "Withdraw",
        [
            "Original position owner only, after maturity",
            "One settlement; checks-effects-interactions + guard",
            "Deposit pause does not prevent matured withdrawal",
        ],
        "shield",
    )
    d.text(
        40,
        562,
        "Sepolia: 6 confirmations. Disposable Anvil demo: 1 confirmation and isolated time advancement.",
        18,
    )
    d.save(folder / "transactions.svg")

    d = Diagram(
        "The security controls we implemented",
        "Each step closes a concrete failure path and has a matching test.",
        660,
    )
    items = [
        (
            "1  Define boundaries",
            ["Account access vs wallet authority", "Keep private keys out of the API"],
            "wallet",
        ),
        (
            "2  Verify identities",
            ["Argon2id passwords + SIWE proofs", "Rate limits and strict input shapes"],
            "user",
        ),
        (
            "3  Control sessions",
            ["Opaque tokens, rotation and expiry", "Origin + CSRF on mutations"],
            "clock",
        ),
        (
            "4  Enforce permissions",
            ["Current roles and wallet ownership", "Invalidate access after changes"],
            "shield",
        ),
        (
            "5  Protect stored data",
            ["AES-256-GCM + bound context", "Reserve nonces and retain key IDs"],
            "key",
        ),
        (
            "6  Add verified MFA",
            ["QR enrollment + one-use factors", "No login/recovery bypass"],
            "qr",
        ),
        (
            "7  Verify the system",
            ["API misuse + browser + contracts", "Record evidence and remaining gaps"],
            "check",
        ),
    ]
    for i, (title, lines, icon) in enumerate(items):
        x, y = 40 + (i % 3) * 390, 125 + (i // 3) * 174
        d.node(x, y, 350, 136, title, lines, icon, i == 6)
    d.save(folder / "security-steps.svg")


if __name__ == "__main__":
    build(Path("output/pdf/hypervaults-report/assets"))
