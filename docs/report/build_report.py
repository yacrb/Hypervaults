"""Build the illustrated application report from verified screenshots and SVG renders.

Run with the bundled document Python runtime after the checks in README.md.
Layout uses explicit page budgets; every figure preserves its original evidence.
"""

import json
import os
from html import escape
from pathlib import Path

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/pdf/hypervaults-report"
ASSETS, EVIDENCE = OUT / "assets", OUT / "evidence"
DEFAULT_FONT = (
    Path.home()
    / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/Resources/fonts/truetype"
)
FONT = Path(os.environ.get("HV_REPORT_FONT_DIR", str(DEFAULT_FONT)))
for name, file in (
    ("Body", "NotoSans-Regular.ttf"),
    ("BodyBold", "NotoSans-Bold.ttf"),
    ("Display", "Rubik-Regular.ttf"),
    ("DisplayBold", "Rubik-Bold.ttf"),
):
    pdfmetrics.registerFont(TTFont(name, FONT / file))
pdfmetrics.registerFontFamily(
    "Body", normal="Body", bold="BodyBold", italic="Body", boldItalic="BodyBold"
)
W, H = A4
M, CONTENT = 44, W - 88
INK, PURPLE, PALE, MUTED = [
    colors.HexColor(c) for c in ("#241B36", "#7954C7", "#F1EBFB", "#6F647C")
]
REFERENCES = [
    (
        "Authentication",
        "https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html",
    ),
    (
        "Session management",
        "https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html",
    ),
    (
        "Password storage",
        "https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html",
    ),
    (
        "Cryptographic storage",
        "https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html",
    ),
    ("Sign-In with Ethereum ERC-4361", "https://eips.ethereum.org/EIPS/eip-4361"),
    (
        "Time-based one-time passwords RFC 6238",
        "https://www.rfc-editor.org/rfc/rfc6238",
    ),
    (
        "OpenZeppelin access control",
        "https://docs.openzeppelin.com/contracts/5.x/access-control",
    ),
    (
        "Local QR generation with Segno",
        "https://segno.readthedocs.io/en/stable/web-development.html",
    ),
    ("Technology logo artwork", "https://github.com/simple-icons/simple-icons"),
]


class Report:
    def __init__(self):
        self.canvas = Canvas(
            str(OUT / "HyperVaults_Report.pdf"), pagesize=A4, pageCompression=1
        )
        self.canvas.setTitle("HyperVaults Authentication and Cryptography")
        self.canvas.setAuthor("Mohamed Yacine Riabi; Yassine ben lazreg")
        self.canvas.setSubject(
            "Application architecture, authentication controls, demo and security validation"
        )
        self.page, self.figure, self.y = 0, 0, H - M

    def check(self, bottom):
        if bottom < 64:
            raise ValueError(
                f"Page {self.page} exceeds its layout budget: {bottom:.1f}"
            )

    def paragraph(
        self,
        text,
        x=M,
        width=CONTENT,
        y=None,
        size=10.4,
        leading=15.5,
        color=INK,
        gap=10,
    ):
        top = self.y if y is None else y
        p = Paragraph(
            text,
            ParagraphStyle(
                "body",
                fontName="Body",
                fontSize=size,
                leading=leading,
                textColor=color,
                alignment=TA_LEFT,
            ),
        )
        _, height = p.wrap(width, 1000)
        self.check(top - height)
        p.drawOn(self.canvas, x, top - height)
        if y is None:
            self.y = top - height - gap
        return height

    def start(self, section, title, subtitle):
        if self.page:
            self.canvas.showPage()
        self.page += 1
        c = self.canvas
        c.setFillColor(colors.HexColor("#FCFBFE"))
        c.rect(0, 0, W, H, fill=1, stroke=0)
        c.bookmarkPage(f"page-{self.page}")
        c.addOutlineEntry(title, f"page-{self.page}", 0)
        c.drawImage(str(ASSETS / "hypervaults.png"), M, H - 44, 20, 20, mask="auto")
        c.setFont("BodyBold", 9)
        c.setFillColor(INK)
        c.drawString(M + 28, H - 37, "HyperVaults")
        c.setFont("Body", 8)
        c.setFillColor(MUTED)
        c.drawRightString(W - M, H - 37, section)
        c.setFont("DisplayBold", 25)
        c.setFillColor(INK)
        if pdfmetrics.stringWidth(title, "DisplayBold", 25) > CONTENT:
            raise ValueError(f"Page {self.page} title is too wide: {title}")
        c.drawString(M, H - 95, title)
        self.y = H - 113
        self.paragraph(subtitle, size=10.2, color=MUTED, leading=15, gap=21)
        c.setFont("Body", 8)
        c.setFillColor(MUTED)
        c.drawString(M, 34, "Authentication and cryptography  |  Application report")
        c.setFillColor(PURPLE)
        c.circle(W - M - 24, 37, 2, fill=1, stroke=0)
        c.setFillColor(INK)
        c.drawRightString(W - M, 34, f"{self.page:02d}")

    def box(self, title, text):
        top = self.y
        style = ParagraphStyle(
            "box", fontName="Body", fontSize=10, leading=15, textColor=INK
        )
        p = Paragraph(text, style)
        _, body_h = p.wrap(CONTENT - 32, 1000)
        height = body_h + 52
        self.check(top - height)
        c = self.canvas
        c.setFillColor(PALE)
        c.roundRect(M, top - height, CONTENT, height, 12, fill=1, stroke=0)
        c.setFont("BodyBold", 11)
        c.setFillColor(PURPLE)
        c.drawString(M + 16, top - 23, title)
        p.drawOn(c, M + 16, top - height + 14)
        self.y = top - height - 15

    def table(self, rows, widths, size=9.4, padding=8):
        styled = []
        for i, row in enumerate(rows):
            style = ParagraphStyle(
                "cell",
                fontName="BodyBold" if i == 0 else "Body",
                fontSize=size,
                leading=size + 4,
                textColor=colors.white if i == 0 else INK,
            )
            styled.append([Paragraph(str(cell), style) for cell in row])
        table = Table(styled, colWidths=widths, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), PURPLE),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F4F0FA")],
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), padding),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#DED5E9")),
                    ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor("#DED5E9")),
                ]
            )
        )
        _, height = table.wrap(CONTENT, 1000)
        self.check(self.y - height)
        table.drawOn(self.canvas, M, self.y - height)
        self.y -= height + 16

    def image(
        self,
        path,
        caption=None,
        width=CONTENT,
        max_height=300,
        x=M,
        top=None,
        crop=None,
    ):
        top = self.y if top is None else top
        with Image.open(path) as im:
            iw, ih = im.size
        left, upper, right, lower = crop or (0, 0, iw, ih)
        scale = min(width / (right - left), max_height / (lower - upper))
        dw, dh = (right - left) * scale, (lower - upper) * scale
        self.check(top - dh)
        c = self.canvas
        c.saveState()
        clipping = c.beginPath()
        clipping.roundRect(x, top - dh, dw, dh, 9)
        c.clipPath(clipping, stroke=0)
        c.drawImage(
            str(path),
            x - left * scale,
            top - dh - (ih - lower) * scale,
            iw * scale,
            ih * scale,
            mask="auto",
        )
        c.restoreState()
        bottom = top - dh
        if caption:
            self.figure += 1
            height = self.paragraph(
                f"<b>Figure {self.figure}.</b> {caption}",
                x=x,
                width=width,
                y=bottom - 9,
                size=8.3,
                leading=12,
                color=MUTED,
            )
            bottom -= height + 9
        if top == self.y:
            self.y = bottom - 15
        return bottom - 15

    def diagram(self, name, caption, max_height=300):
        return self.image(ASSETS / f"{name}.png", caption, max_height=max_height)

    def screen(self, name, caption, **kwargs):
        return self.image(EVIDENCE / f"{name}.png", caption, **kwargs)

    def heading(self, text):
        self.canvas.setFont("BodyBold", 12)
        self.canvas.setFillColor(PURPLE)
        self.canvas.drawString(M, self.y - 13, text)
        self.y -= 27

    def cover(self):
        c = self.canvas
        self.page = 1
        c.bookmarkPage("page-1")
        c.addOutlineEntry("HyperVaults", "page-1", 0)
        c.setFillColor(colors.HexColor("#101018"))
        c.rect(0, 0, W, H, fill=1, stroke=0)
        c.saveState()
        c.setFillAlpha(0.18)
        for radius, color in ((315, "#3A225B"), (245, "#59378D"), (173, "#7954C7")):
            c.setFillColor(colors.HexColor(color))
            c.circle(W - 42, 470, radius, fill=1, stroke=0)
        c.restoreState()
        c.drawImage(str(ASSETS / "hypervaults.png"), M, H - 105, 47, 47, mask="auto")
        c.setFont("BodyBold", 11)
        c.setFillColor(colors.HexColor("#BDA5EF"))
        c.drawString(M + 64, H - 86, "APPLICATION REPORT")
        c.setFont("DisplayBold", 49)
        c.setFillColor(colors.white)
        c.drawString(M, H - 192, "HyperVaults")
        c.setFont("Display", 22)
        c.setFillColor(colors.HexColor("#CDB9FA"))
        c.drawString(M, H - 231, "Authentication and cryptography")
        self.paragraph(
            "Architecture, security controls and a verified application demo",
            y=H - 252,
            size=12,
            leading=18,
            color=colors.HexColor("#CAC4D5"),
        )
        c.drawImage(
            str(ASSETS / "vault-art.png"),
            M + 120,
            265,
            300,
            230,
            mask="auto",
            preserveAspectRatio=True,
            anchor="c",
        )
        c.setFillColor(colors.HexColor("#272032"))
        c.roundRect(M, 102, CONTENT, 127, 18, fill=1, stroke=0)
        c.setFont("BodyBold", 15)
        c.setFillColor(colors.white)
        c.drawString(M + 22, 197, "Mohamed Yacine Riabi")
        c.drawString(M + 22, 170, "Yassine ben lazreg")
        for i, name in enumerate(
            ("python", "fastapi", "mongodb", "ethereum", "docker", "solidity")
        ):
            x = M + i * 85
            c.drawImage(str(ASSETS / f"{name}.png"), x, 52, 19, 19, mask="auto")
            c.setFont("Body", 7.1)
            c.setFillColor(colors.HexColor("#D5CCDF"))
            c.drawString(
                x + 24,
                58,
                "FastAPI"
                if name == "fastapi"
                else "MongoDB"
                if name == "mongodb"
                else name.title(),
            )

    def build(self):
        self.cover()
        self.start(
            "01  Product",
            "What HyperVaults does",
            "A wallet-based test-token vault with a separate website identity and security system.",
        )
        self.paragraph(
            "HyperVaults lets a member compare fixed lock offers, deposit HVT, track a funded reward and withdraw after maturity. Rewards come from a sponsor reserve; locking does not create investment yield. The current app uses Python/FastAPI, MongoDB, plain HTML/CSS/JavaScript and Solidity contracts. The selected public network is Sepolia."
        )
        self.paragraph(
            "We protected the website account without putting wallet private keys in the server. Email and wallet sign-in resolve one account and pass through the same optional MFA gate. The browser wallet still independently authorizes financial transactions. Private notes and MFA secrets are encrypted before being stored."
        )
        self.box(
            "Verified outcome",
            "54 Python checks and 16 Solidity checks passed, including 512 randomized reward inputs. The real browser demo completed four disposable-chain transactions and 20 responsive page checks. A dependency audit covered 61 runtime packages and found no known advisories.",
        )
        self.heading("Reading guide")
        self.table(
            [
                ["Report section", "Pages", "What it explains"],
                [
                    "System and authentication",
                    "3-7",
                    "Trust boundaries, credentials, sessions, wallet linking and MFA enrollment.",
                ],
                [
                    "MFA and cryptography",
                    "8-10",
                    "Actual setup/sign-in screens and authenticated encryption.",
                ],
                [
                    "Authorization and transactions",
                    "11-12",
                    "Roles, ownership, reserves and canonical receipt verification.",
                ],
                [
                    "Application demo",
                    "13-17",
                    "Registration through funding, deposits, notes, settlement and administration.",
                ],
                [
                    "Security assessment",
                    "18-21",
                    "Implementation steps, negative tests, evidence and remaining limitations.",
                ],
                [
                    "Live demonstration and sources",
                    "22-23",
                    "A practical demo script, design justifications and reproducible checks.",
                ],
            ],
            [150, 50, CONTENT - 200],
            size=9.2,
        )
        self.paragraph(
            "<b>Evidence scope.</b> Demo screenshots show the actual app running with disposable accounts and real contracts on an isolated Anvil node. The wallet provider is a test bridge to that node's unlocked accounts. Existing Sepolia contracts were checked read-only; no live funds or account security settings were changed.",
            size=9.2,
            leading=14,
        )

        self.start(
            "02  Architecture",
            "Architecture and trust boundaries",
            "The browser presents the workflow; the API enforces access; the contracts enforce financial ownership.",
        )
        self.diagram(
            "architecture",
            "Architecture of the running Sepolia app. The disposable demo follows the same boundaries on Anvil.",
            max_height=326,
        )
        self.paragraph(
            "<b>Browser.</b> Sign-in, account security and the normal dashboard use separate pages and JavaScript entry points. This keeps authentication controls usable when wallet configuration is unavailable. Connecting a wallet and signing in to the website are separate actions."
        )
        self.paragraph(
            "<b>Server and data.</b> FastAPI handles identity proofs, sessions, permissions, notes and chain verification. MongoDB stores users, hashed session/reset identifiers, encrypted secrets, audit events and an event index. Unique email and wallet indexes stop silent account merging; expiry indexes clean transient records."
        )
        self.paragraph(
            "<b>Network and keys.</b> Docker runs the app and MongoDB on a private bridge with localhost-only published ports. The server reads the configured RPC and checks deployment fingerprints. AES keys live outside MongoDB in the external key ring. The browser wallet holds transaction signing authority."
        )

        self.start(
            "03  Authentication",
            "How sign-in is protected",
            "Both primary methods reach the same server-controlled second-factor decision.",
        )
        self.diagram(
            "sign-in",
            "A correct password or wallet signature creates only an anonymous pending session when MFA is enabled.",
            max_height=294,
        )
        self.paragraph(
            "<b>Email registration.</b> Input schemas reject extra fields and malformed values. New passwords require 12-128 characters; accounts receive the member role on the server. Email syntax is validated and normalized, but email ownership verification is not implemented. The unique index prevents duplicate identities."
        )
        self.paragraph(
            "<b>Password verification.</b> We use the library's recommended Argon2id password hashing and rehash support. The database contains a salted encoded hash, not a plaintext password or reversible password encryption. Unknown accounts run a dummy hash verification. Invalid or disabled identities receive a generic login failure."
        )
        self.box(
            "Abuse control",
            "Separate IP and identity budgets limit password and factor guesses. Login permits five identity attempts per minute; invalid shapes are refused before account data is used. These controls reduce guessing, but they do not make passwords or TOTP phishing-resistant.",
        )
        self.paragraph(
            "Design references: OWASP authentication and password storage [1, 3].",
            size=8.4,
            color=MUTED,
        )

        self.start(
            "04  Sessions",
            "How sessions stay controlled",
            "The browser gets an opaque random identifier; account permissions remain on the server.",
        )
        self.diagram(
            "sessions",
            "Session rotation, current-account checks and revocation prevent stale identity state from granting access.",
            max_height=248,
        )
        self.table(
            [
                ["Control", "Implementation", "Why it matters"],
                [
                    "Token storage",
                    "32 random bytes; SHA-256 identifier in MongoDB",
                    "A database row does not expose the raw browser session token.",
                ],
                [
                    "Cookie policy",
                    "HttpOnly and SameSite=Strict; Secure and __Host- on HTTPS",
                    "Restricts script access and cross-site cookie use.",
                ],
                [
                    "Lifetime",
                    "30-minute idle / 8-hour absolute",
                    "Activity cannot extend the fixed absolute expiry.",
                ],
                [
                    "Reauthentication",
                    "Five-minute recent proof for sensitive settings",
                    "An old session alone cannot authorize a security change.",
                ],
                [
                    "Version",
                    "Current auth_version and disabled state checked per request",
                    "Password, linkage, MFA and admin changes invalidate affected access.",
                ],
                [
                    "Logout",
                    "Delete server session and clear cookie",
                    "Replaying the old identifier returns 401.",
                ],
            ],
            [99, 205, CONTENT - 304],
            size=8.6,
        )
        self.paragraph(
            "Mutations also require the exact configured Origin and a matching current CSRF token. CSP blocks external scripts, framing and base-URL changes; no-referrer, nosniff and no-store headers protect the page boundary. HTTPS configuration adds HSTS. Public HTTPS hosting itself was not exercised [2].",
            size=9.4,
            leading=14,
        )

        self.start(
            "05  Wallet identity",
            "How wallet linking works",
            "The wallet proves control of an address without authorizing a token transaction.",
        )
        self.diagram(
            "wallet-linking",
            "Email-first and wallet-first users keep one identity while the wallet remains the transaction signer.",
            max_height=244,
        )
        self.paragraph(
            "The server creates a Sign-In with Ethereum message containing its domain, URI, configured chain, nonce, time window and intended action. The stored challenge also binds the browser session, account and authorization version. Verification compares the exact message and recovers the signer before atomically consuming the challenge [5]."
        )
        self.box(
            "Replay and impersonation are refused",
            "Tampering with the message, signing with another wallet, using an expired challenge or repeating a consumed proof fails. Linking requires recent website identity and a signature from the wallet being added. A unique multikey index prevents the same address belonging to two accounts.",
        )
        self.paragraph(
            "Injected key-controlled wallets are supported, including exact EIP-7702 delegation indicators signing with the account's own key. The app rejects contract-wallet signature modes it does not implement. Wallet/provider/account/network changes invalidate the browser's transaction context."
        )
        self.paragraph(
            "Wallet-only members can add email access after recent linked-wallet reauthentication. Enabled MFA applies to that proof. The server refuses removal of the final sign-in method and limits an account to eight linked wallets."
        )

        self.start(
            "06  MFA",
            "How MFA enrollment is secured",
            "We activate protection only after the authenticator demonstrates it holds the new secret.",
        )
        self.diagram(
            "mfa-enrollment",
            "QR setup, encrypted enrollment, verified activation and one-use recovery codes.",
            max_height=235,
        )
        self.paragraph(
            "Setup first confirms the user's password or linked-wallet identity. A fresh 160-bit secret is encrypted in a browser/account/version-bound enrollment record with a five-minute expiry. The app generates a black-on-white PNG QR locally from the standard otpauth URI; the secret never goes to an external QR service."
        )
        self.paragraph(
            "The QR response is authenticated, CSRF-protected and marked no-store. The same secret is available under <b>Can't scan?</b> as a manual fallback. Canceling, enabling MFA or leaving the page removes the key and image from the page. There is no public image URL and no browser storage of the enrollment secret."
        )
        self.paragraph(
            "The server checks the first six-digit authenticator code before enabling MFA. TOTP uses HMAC-SHA1, 30-second steps and a one-step drift allowance. Atomic last-step advancement rejects replay. Eight 128-bit recovery codes are displayed once; MongoDB stores only their SHA-256 hashes and atomic removal makes them single-use [6, 8]."
        )
        self.box(
            "Changing MFA needs a full identity proof",
            "Turn off and New recovery codes require password or linked-wallet reauthentication plus an enabled factor. A 60-second confirmation is bound to that named action and consumed once. MFA changes revoke other sessions and stale enrollment/reset state.",
        )

        self.start(
            "07  MFA demo",
            "Scan then confirm then save",
            "Actual enrollment screens from the disposable demo account.",
        )
        top = self.y
        left = self.screen(
            "13-mfa-qr",
            "Scan the local QR and enter the code.",
            width=239,
            max_height=307,
            top=top,
            crop=(0, 0, 575, 637),
        )
        right = self.screen(
            "14-recovery-codes",
            "Download the eight one-use backup codes.",
            width=239,
            x=M + 267,
            max_height=307,
            top=top,
            crop=(0, 0, 575, 590),
        )
        self.y = min(left, right) - 5
        self.paragraph(
            "<b>1. Open Security.</b> Choose Set up authenticator and confirm the existing identity. Account controls sit outside the dashboard layout; there are no balances or financial actions in this enrollment form."
        )
        self.paragraph(
            "<b>2. Scan and enter a code.</b> Import the QR into an authenticator, then supply its six-digit code. The UI keeps the manual key collapsed. If the image cannot load, the manual fallback remains usable."
        )
        self.paragraph(
            "<b>3. Save recovery codes.</b> Protection is now active. Download the codes, store them privately and dismiss the one-time display. New codes replace the old set; each works only once."
        )
        self.box(
            "QR correctness was tested",
            "An independent reader decoded the actual rendered QR at 1280, 768, 375 and 320-pixel widths. The scanned secret generated a code that enabled MFA through the real browser and API. A physical phone-authenticator scan was not performed.",
        )
        self.paragraph(
            "The pictured enrollment and recovery codes belong to an account deleted when the isolated test ended. They are not credentials for the running Sepolia app.",
            size=8.5,
            color=MUTED,
            leading=13,
        )

        self.start(
            "08  MFA sign-in",
            "A primary proof is not enough",
            "The second-factor screen is the only route from pending identity to account privileges.",
        )
        top = self.y
        bottom = self.screen(
            "16-mfa-signin",
            "A wrong code is refused without signing the account in.",
            width=244,
            max_height=288,
            top=top,
            crop=(814, 310, 1247, 760),
        )
        self.paragraph(
            "<b>Email path.</b> A correct password rotated the browser into a pending MFA session. Calling /api/security/sessions from that same browser returned <b>401</b> until the second factor succeeded.",
            x=M + 267,
            width=239,
            y=top,
            size=10.1,
        )
        self.paragraph(
            "<b>Wallet path.</b> The linked-wallet SIWE proof reached the same MFA screen. A recovery code completed that sign-in; the server retained the same user account.",
            x=M + 267,
            width=239,
            y=top - 136,
            size=10.1,
        )
        self.y = bottom - 5
        self.paragraph(
            "Wrong, expired or already-used codes keep the user in the verification step. Submission guards prevent duplicate sign-ins while the first request is in flight. Use a recovery code offers an explicit alternate factor; Back to sign in cancels the pending flow."
        )
        self.box(
            "Recovery cannot bypass MFA",
            "A password reset link still needs the enabled second factor, keeps MFA enabled and revokes old access. Wallet ownership cannot disable MFA on its own. Losing both the authenticator and all recovery codes has no automatic website bypass.",
        )
        self.paragraph(
            "MFA protects website access. A wallet can transact directly on-chain without the website, so website MFA cannot replace wallet transaction approval or protect a compromised wallet key."
        )

        self.start(
            "09  Cryptography",
            "How stored data is protected",
            "Authenticated encryption binds a record to its intended account and use.",
        )
        self.diagram(
            "encryption",
            "AES-256-GCM protects confidentiality and refuses ciphertext/context tampering.",
            max_height=260,
        )
        self.paragraph(
            "Private notes and MFA secrets use AES-256-GCM with fresh 12-byte nonces. A shared persistent reservation prevents nonce reuse; exhausted collision retries fail closed. MongoDB stores ciphertext, tag, nonce and key ID. Keys stay outside the database, and old keys must be retained during rotation [4]."
        )
        self.paragraph(
            "Associated data binds purpose, user and record. Notes identify their deployment and position; enrollment uses its own identity. Swapping ciphertext across accounts or purposes invalidates the tag. Authorization is rechecked after slow chain reads before a note is decrypted."
        )
        self.table(
            [
                ["Primitive", "What it does here", "What it does not do"],
                [
                    "Argon2id",
                    "One-way password hashing with salt and rehash support",
                    "It is not reversible password encryption.",
                ],
                [
                    "AES-256-GCM",
                    "Encrypt notes and MFA secrets with integrity and bound context",
                    "It is not end-to-end encryption; the server can decrypt.",
                ],
                [
                    "HMAC-SHA1 TOTP",
                    "Generate short-lived authenticator codes",
                    "It does not encrypt stored notes or resist phishing by itself.",
                ],
                [
                    "Ethereum signatures",
                    "Prove wallet control and authorize wallet transactions",
                    "They do not grant website admin roles.",
                ],
            ],
            [90, 210, CONTENT - 300],
            size=8.7,
        )

        self.start(
            "10  Authorization",
            "Who may do what",
            "Roles are checked by the API and ownership is checked independently by Solidity.",
        )
        self.diagram(
            "authorization",
            "Website member, website administrator and contract authority have distinct permissions.",
            max_height=214,
        )
        self.table(
            [
                ["Action", "Required authority", "Enforcement"],
                [
                    "Account settings and sessions",
                    "Signed-in account owner",
                    "Current user, recent identity and factor checks where required.",
                ],
                [
                    "Private position note",
                    "Account with the position owner's linked wallet",
                    "Owner lookup, current linkage/version and GCM authentication.",
                ],
                [
                    "User roles, disablement and audit",
                    "Current website administrator",
                    "Server role dependency, self-change restriction and serialized admin updates.",
                ],
                [
                    "Future offers and deposit pause",
                    "Contract owner's wallet",
                    "Ownable2Step; website UI additionally requires an admin account.",
                ],
                [
                    "Matured withdrawal",
                    "Original position owner's wallet",
                    "On-chain owner, maturity and one-time settlement checks.",
                ],
            ],
            [139, 160, CONTENT - 299],
            size=9,
        )
        self.paragraph(
            "A hidden Admin navigation item is a convenience, not the security boundary. Direct admin requests from a member return 403. An incomplete MFA identity receives 401. Guessing another user's session ID cannot revoke it, and knowing a position ID does not disclose its private note."
        )

        self.start(
            "11  Chain integrity",
            "How financial actions are verified",
            "A submitted transaction must match the network, signer, destination and resulting contract event.",
        )
        self.diagram(
            "transactions",
            "The reviewed action becomes confirmed only after canonical receipt and event verification.",
            max_height=252,
        )
        self.paragraph(
            "Before signing, the browser checks the selected wallet and chain plus the deployment anchor and contract code hashes. Anvil requires the recorded genesis; Sepolia uses the recorded deployment block. Mainnet is rejected. Account, provider or network changes invalidate the prepared context."
        )
        self.paragraph(
            "The wallet signs the transaction. The backend holds no wallet private keys and has no transaction-signing endpoint. It checks receipt status, canonical block hash, configured confirmation depth, signer, expected target and matching event fields. Sepolia requires six confirmations; a failed or unavailable verification cannot count as success."
        )
        self.box(
            "Contract safety rules",
            "The entire fixed reward must already be funded at deposit. Reviewed duration/rate prevent silent term changes. Existing positions keep their terms. Withdrawals require the original owner and maturity, settle once and stay available while new deposits are paused. SafeERC20, exact balance deltas and ReentrancyGuard support these checks [7].",
        )
        self.paragraph(
            "Reward calculation uses integer token base units and floors once: principal × APR basis points × duration / (10,000 × 365 days). Sponsor funding cannot be recovered, and the non-upgradeable vault has no seizure, sweep or early-withdrawal function."
        )

        self.start(
            "12  Demo",
            "Create an account and link a wallet",
            "Email access starts the website session; the signed wallet link enables the financial workflow.",
        )
        top = self.y
        bottom = self.screen(
            "02-registration",
            "Registration collects a display name, email and matching passwords.",
            width=239,
            max_height=325,
            top=top,
            crop=(814, 142, 1248, 873),
        )
        self.paragraph(
            "<b>1. Register.</b> The browser submitted the disposable member through the real API. The server assigned the member role and stored an Argon2id password hash. It rotated the session after success.",
            x=M + 267,
            width=239,
            y=top,
        )
        self.paragraph(
            "<b>2. Open the dashboard.</b> Email sign-in creates website access. The account initially has no linked wallet. The dashboard offers a short Connect, Link, Ready guide.",
            x=M + 267,
            width=239,
            y=top - 135,
        )
        self.paragraph(
            "<b>3. Connect.</b> Choose an available wallet. Reading its address prepares the link step; it has not yet changed account ownership.",
            x=M + 267,
            width=239,
            y=top - 245,
        )
        self.y = bottom - 5
        self.screen(
            "04-wallet-connected",
            "Connecting shows Link wallet; it has not yet changed account ownership.",
            max_height=125,
            crop=(243, 175, 1415, 338),
        )
        self.paragraph(
            "Connect discovered the test provider and selected the isolated network. The account still had no linked wallets. Link wallet then obtained and verified a real SIWE signature from the disposable node's unlocked account; only that proof attached the address. The onboarding guide disappeared after success."
        )
        self.paragraph(
            "The provider is a testing bridge, not a live MetaMask or Rabby extension. The actual app's wallet discovery, signature verification and account-linking code executed unchanged.",
            size=8.6,
            leading=13,
            color=MUTED,
        )

        self.start(
            "13  Demo",
            "Review a funded deposit",
            "The user sees the offer and exact amounts before the wallet sends approval and deposit transactions.",
        )
        self.screen(
            "05-vaults",
            "Three open offers in the isolated demo.",
            max_height=195,
            crop=(240, 85, 1415, 620),
        )
        top = self.y
        bottom = self.screen(
            "06-deposit-review",
            "Review lists signer, contracts, principal and fixed reward.",
            width=240,
            max_height=307,
            top=top,
        )
        self.paragraph(
            "<b>Get HVT.</b> The demo claimed 1,000 test tokens through the browser. The receipt and faucet event were verified by the API.",
            x=M + 268,
            width=238,
            y=top,
            size=10.2,
        )
        self.paragraph(
            "<b>Preview 100 HVT.</b> The selected one-day demo offer used a 3% annualized simple rate. The reserve covered its fixed reward. The preview explicitly listed both contracts and the selected signer.",
            x=M + 268,
            width=238,
            y=top - 100,
            size=10.2,
        )
        self.paragraph(
            "<b>Confirm in the wallet.</b> The browser set an exact 100 HVT allowance, then deposited. The consumed allowance returned to zero. Both successful transactions passed receipt and event checks.",
            x=M + 268,
            width=238,
            y=top - 217,
            size=10.2,
        )
        self.y = bottom - 8
        self.paragraph(
            "The fixture's offer terms are demo inputs; they are not a promise about live investment returns. HVT has no price and is an educational test token.",
            size=8.7,
            leading=13,
            color=MUTED,
        )

        self.start(
            "14  Demo",
            "Track a position and save a note",
            "The dashboard reads contract state while private text is protected by the account API.",
        )
        self.screen(
            "07-overview",
            "Confirmed state shows 100 HVT principal and its sponsor-reserved reward.",
            max_height=230,
            crop=(218, 70, 1419, 680),
        )
        self.paragraph(
            "The overview displayed 100 HVT locked and approximately 0.0082 HVT reserved for the one-day offer. Exact amounts remain available in the position details. The withdrawal control was disabled before on-chain maturity; clicking a different website role could not change that rule."
        )
        self.screen(
            "09-encrypted-note",
            "The real API acknowledged encrypted storage of the account's note.",
            width=292,
            max_height=200,
        )
        self.paragraph(
            "The browser saved the note through the real API. MongoDB contained ciphertext, not the note text. Adversarial tests refused cross-account reads/writes, modified ciphertext and swapped context. Authorized server code can decrypt the note; this is storage encryption."
        )

        self.start(
            "15  Demo",
            "Withdraw after maturity",
            "The original wallet settles once, including while new deposits are paused.",
        )
        top = self.y
        left = self.screen(
            "10-withdrawal-review",
            "The owner reviews principal, reserved reward and payout.",
            width=240,
            max_height=321,
            top=top,
        )
        right = self.screen(
            "12-mobile-positions",
            "The settled position also renders on a narrow screen.",
            width=205,
            x=M + 284,
            max_height=395,
            top=top,
        )
        self.y = min(left, right) - 6
        self.paragraph(
            "To test the full lifecycle without a real waiting period, the harness advanced time only on its disposable Anvil node. It paused new deposits, reached the one-day position's maturity and submitted withdrawal from the original owner wallet. No public-network time manipulation or Sepolia withdrawal was attempted."
        )
        self.box(
            "Settlement checks passed",
            "The backend verified the successful PositionSettled event. Outstanding principal and committed rewards returned to zero, and the owner received principal plus the reserved reward. Contract tests separately rejected early, repeated and other-wallet withdrawals.",
        )

        self.start(
            "16  Demo",
            "Administration stays separate",
            "Website account controls and contract-owner controls use different authority checks.",
        )
        self.screen(
            "17-admin-contract",
            "A disposable website admin can inspect coverage without being the contract owner.",
            max_height=250,
            crop=(220, 80, 1420, 760),
        )
        self.screen(
            "18-admin-accounts",
            "The account directory is available only through administrator-protected APIs.",
            max_height=203,
            crop=(245, 90, 1415, 540),
        )
        self.paragraph(
            "The harness bootstrapped a separate administrator only inside the disposable database. Contract controls still required the owner's wallet, which that admin did not hold. Member requests to the same admin endpoints returned 403. Disabling an account or changing its access version revoked affected sessions."
        )
        self.paragraph(
            "No real user role, password, MFA state or linked wallet was edited to produce these screenshots. Administration is protected by current role and recent identity; mandatory MFA for administrators is a remaining improvement.",
            size=9.2,
            leading=14,
        )

        self.start(
            "17  Security design",
            "The steps we took",
            "Security decisions were implemented as explicit boundaries, then checked against misuse cases.",
        )
        self.diagram(
            "security-steps",
            "Seven implementation steps connect the architecture to concrete security controls.",
            max_height=282,
        )
        self.table(
            [
                ["Threat", "Control we applied", "Evidence"],
                [
                    "Password theft from stored data",
                    "Argon2id salted password hash",
                    "Hash prefix and plaintext absence checked.",
                ],
                [
                    "Session fixation or stale access",
                    "Rotation, expiry, live status/version checks",
                    "Old cookie replay and revoked access refused.",
                ],
                [
                    "Cross-site state changes",
                    "Strict Origin + current CSRF token",
                    "Wrong/missing origin or token returns 403.",
                ],
                [
                    "Identity impersonation or proof replay",
                    "Bound SIWE message and atomic consumption",
                    "Wrong signer, tampering and repeat proof refused.",
                ],
                [
                    "Unauthorized reads or tampered storage",
                    "Ownership checks + context-bound AES-GCM",
                    "Cross-account note access and tag changes refused.",
                ],
                [
                    "Primary-factor-only access",
                    "Anonymous pending MFA + one-use factors",
                    "Protected APIs return 401 before factor completion.",
                ],
            ],
            [140, 192, CONTENT - 332],
            size=8.6,
        )

        self.start(
            "18  Security tests",
            "Negative tests and expected results",
            "These cases exercise the server and storage layer directly, beyond what the interface allows.",
        )
        self.table(
            [
                ["Security case", "Expected result", "Observed"],
                [
                    "Registration tries role=admin, malformed email or short password",
                    "Validation refusal; no privileged account",
                    "PASS / 422",
                ],
                [
                    "Unknown account vs wrong password",
                    "Same generic login failure",
                    "PASS / 401",
                ],
                [
                    "Repeated incorrect password or MFA guesses",
                    "Identity/IP budget limits attempts",
                    "PASS / 429",
                ],
                [
                    "Old rotated cookie; idle/absolute expiry; disabled account",
                    "No protected account access",
                    "PASS / 401",
                ],
                [
                    "Missing CSRF, attacker Origin, oversize or non-JSON body",
                    "Reject state-changing request",
                    "PASS / 403, 413, 415",
                ],
                [
                    "Member calls admin APIs; other account revokes a session",
                    "No privilege or cross-account mutation",
                    "PASS / 403, 404",
                ],
                [
                    "Altered SIWE, wrong signer, expired or replayed challenge",
                    "No wallet login/link proof accepted",
                    "PASS / 400",
                ],
                [
                    "Same wallet linked to a second account; final login removed",
                    "Unique ownership and access retention",
                    "PASS / 409",
                ],
                [
                    "Primary sign-in with MFA unfinished or expired",
                    "Anonymous pending state; no permissions",
                    "PASS / 401, 400",
                ],
                [
                    "Concurrent reuse of TOTP or recovery code",
                    "Exactly one factor consumption succeeds",
                    "PASS / atomic Mongo update",
                ],
                [
                    "Wrong MFA action or confirmation after 60 seconds",
                    "Settings change refused",
                    "PASS / 403",
                ],
                [
                    "Password recovery without the enabled factor; reused token",
                    "No MFA bypass or second reset",
                    "PASS / 403, 400",
                ],
                [
                    "Other-account note, tampered ciphertext, swapped AAD",
                    "No plaintext or successful decryption",
                    "PASS / 404, 409",
                ],
                [
                    "Access revoked during chain read; repeated encryption nonce",
                    "Refuse stale access or unsafe encryption",
                    "PASS / 401, failure",
                ],
            ],
            [244, 151, CONTENT - 395],
            size=8.6,
            padding=7,
        )
        self.paragraph(
            "The API suite uses a uniquely named real MongoDB database for each case, not an in-memory database replacement. Cleanup removes only those disposable test databases. Tests also confirm private recovery outbox permissions and key-rotation compatibility.",
            size=9.1,
            leading=14,
        )

        self.start(
            "19  Verification",
            "Results and strength of evidence",
            "Passing checks demonstrate specific behavior; coverage and scanning provide additional diagnostics.",
        )
        self.table(
            [
                ["Layer", "Result", "What was exercised"],
                [
                    "Python API and algorithms",
                    "54 / 54 passed",
                    "Passwords, sessions, roles, CSRF, SIWE, MFA, encryption and network/setup guards.",
                ],
                [
                    "Solidity behavior",
                    "16 / 16 passed",
                    "Ownership, maturity, reserves, pause, terms, fee-token refusal and settlement.",
                ],
                [
                    "Reward arithmetic fuzzing",
                    "512 inputs passed",
                    "Bounded randomized principal, duration and rate; exact integer-floor result.",
                ],
                [
                    "Browser journeys",
                    "Passed; no page errors",
                    "Registration, linking, faucet, exact approval, deposit, note, withdrawal and both MFA logins.",
                ],
                [
                    "Responsive layout",
                    "20 checks passed",
                    "Five pages at 1440, 768, 375 and 320 pixels; no horizontal overflow.",
                ],
                [
                    "Runtime dependencies",
                    "61 packages / 0 known advisories",
                    "Pinned non-development dependency set checked with pip-audit.",
                ],
                [
                    "Running Sepolia app",
                    "Read-only checks passed",
                    "Health, sign-in/out, CSRF refusal, security APIs, 3 offers and reserve coverage.",
                ],
            ],
            [140, 133, CONTENT - 273],
            size=9,
        )
        self.box(
            "Coverage is not a security guarantee",
            "Combined Python branch coverage for auth/, webapp/ and the Sepolia setup script is 77%. Access dependencies are 100%; MFA is 93%; the authorized app routes are 95%. Solidity coverage reports 90.65% lines for the vault, with lower branch coverage and instrumentation anchor warnings. Those diagnostics do not replace the asserted behavior tests.",
        )
        self.paragraph(
            "The browser financial demo used four genuine disposable-chain transaction receipts. The real Sepolia verification used six-confirmation configuration and read-only current state. Physical wallet-extension approval and a phone-authenticator scan were not replayed for this report.",
            size=9.2,
            leading=14,
        )

        self.start(
            "20  Operations",
            "Recovery and operating boundaries",
            "Preserve the state needed to verify data and retain access to encrypted records.",
        )
        self.paragraph(
            "<b>Account recovery.</b> Reset tokens are random, hashed, expire after 15 minutes and are consumed once. The link carries its token in a URL fragment to avoid putting it in HTTP request URLs and referrers. Development messages use a private local outbox; production code uses SMTP with STARTTLS. Enabled MFA stays required during password reset."
        )
        self.paragraph(
            "<b>Backups.</b> Preserve the database, external key ring and deployment manifests together. Keep older AES key IDs until all records using them have been migrated. Restoring ciphertext without its key, or replacing a local chain while retaining an old fingerprint, cannot restore valid access to that data."
        )
        self.paragraph(
            "<b>Chain outages.</b> Password and MFA account controls remain usable when wallet configuration is unavailable. Financial actions require verified chain state and stop on mismatched fingerprints or unsupported networks. A position owner can use the contract directly for a matured withdrawal even if the website account is disabled."
        )
        self.table(
            [
                ["Remaining boundary", "Current status and next action"],
                [
                    "Administrator MFA",
                    "MFA is optional for all accounts. Require it for privileged accounts before public use.",
                ],
                [
                    "Email assurance",
                    "Syntax and uniqueness are checked. Add email ownership verification and verify real SMTP delivery.",
                ],
                [
                    "Public hosting",
                    "Loopback deployment tested. Validate public HTTPS, certificate configuration, database access and backup restore.",
                ],
                [
                    "Password policy",
                    "12-character minimum; MFA is optional. Consider a stronger password-only policy and compromised-password screening.",
                ],
                [
                    "Phishing resistance",
                    "Passwords and TOTP can be phished. A future passkey/WebAuthn option would strengthen account access.",
                ],
                [
                    "Independent assessment",
                    "No independent audit, Burp engagement or production load/security acceptance was performed.",
                ],
            ],
            [145, CONTENT - 145],
            size=9.3,
        )
        self.paragraph(
            "HVT is unpriced and the contracts are unaudited. Test success does not establish readiness for real funds or production. Public RPC responsiveness also remains an operating dependency.",
            size=9.2,
            leading=14,
            color=MUTED,
        )

        self.start(
            "21  Demonstration",
            "A clear live demo and justification",
            "Show each boundary working, then explain the decision that enforces it.",
        )
        self.table(
            [
                ["Demo step", "Show", "Explain"],
                [
                    "1  Register and sign in",
                    "Create a member; inspect a generic failed login",
                    "The server hashes the password and assigns the role.",
                ],
                [
                    "2  Connect and link",
                    "Address appears first; signature links it",
                    "Connection alone does not change website ownership.",
                ],
                [
                    "3  Activate MFA",
                    "Confirm identity, scan QR, verify code, save backups",
                    "Activation requires proof of the new secret; backups are one-use.",
                ],
                [
                    "4  Challenge sign-in",
                    "Password succeeds; protected API still returns 401",
                    "The pending session is anonymous until the factor passes.",
                ],
                [
                    "5  Review a deposit",
                    "Exact terms, network, signer and token allowance",
                    "The wallet signs; a hash alone does not establish completion.",
                ],
                [
                    "6  Save a private note",
                    "Readable UI text; encrypted database record",
                    "Account/position/purpose-bound GCM rejects swaps and tampering.",
                ],
                [
                    "7  Settle and inspect roles",
                    "Owner withdrawal at maturity; member admin request refused",
                    "Contract ownership, account ownership and website roles are independent.",
                ],
            ],
            [119, 190, CONTENT - 309],
            size=9.1,
        )
        self.heading("Design decisions to defend")
        self.paragraph(
            "<b>Opaque sessions.</b> Server-side state supports immediate revocation and current account checks. <b>Argon2id.</b> A memory-hard password hash avoids plaintext/reversible password storage. <b>Bound signatures.</b> SIWE proves a specific wallet action in a specific browser/time context. <b>AES-GCM.</b> Encryption and authentication detect stored-data tampering. <b>Anonymous MFA pending state.</b> No application privilege exists before all required factors pass."
        )
        self.box(
            "Keep the demonstration reproducible",
            "Use disposable test accounts and an isolated Anvil node for deposits, time advancement and withdrawal. Use the running Sepolia app for read-only deployment and account-screen verification. Never paste a real wallet private key or seed phrase into a script, app or report.",
        )

        self.start(
            "22  Sources",
            "Sources and reproducible checks",
            "The report links the implemented controls to local source, tests and primary specifications.",
        )
        self.paragraph(
            "<b>Local source.</b> auth/services.py and auth/routes.py implement identity/session controls; auth/mfa.py and auth/mfa_routes.py implement MFA; auth/crypto.py and webapp/routes.py protect notes. webapp/blockchain.py verifies canonical chain reads/receipts. smartcontract/src/ contains the token and vault. tests/test_security.py and smartcontract/test/HyperVaults.t.sol add the broader security checks.",
            size=9.1,
            leading=14,
        )
        self.table(
            [
                ["Check", "Run from the repository"],
                [
                    "API and algorithm suite",
                    "HV_AUTH_TEST_MONGO_URI=mongodb://127.0.0.1:27018<br/>uv run python -m unittest discover -s tests -v<br/>(set the variable for the same command invocation)",
                ],
                [
                    "Contract suite",
                    ".tools/bin/forge test --root smartcontract --fuzz-runs 512 -vv",
                ],
                [
                    "Real browser app demo",
                    "With HV_AUTH_TEST_MONGO_URI set:<br/>uv run --with playwright python -m tests.check_app_e2e",
                ],
                [
                    "Focused browser checks",
                    "uv run --with playwright python tests/check_auth_ui.py<br/>uv run --with playwright python tests/check_account_ui.py",
                ],
            ],
            [117, CONTENT - 117],
            size=8.3,
        )
        for i, (title, url) in enumerate(REFERENCES, 1):
            self.paragraph(
                f'<b>[{i}]</b> <a href="{escape(url, quote=True)}" color="#7954C7">{escape(title)}</a>'
                + (" - OWASP Cheat Sheet Series" if i <= 4 else ""),
                size=8.9,
                leading=13,
                gap=5,
            )
        self.paragraph(
            "Technology marks are used to identify the actual stack; they do not imply endorsement. SVG artwork comes from Simple Icons, with its CC0 artwork license and each brand's separate trademark guidelines. HyperVaults artwork comes from the repository. All nine report diagrams remain editable SVG files.",
            size=8.4,
            leading=13,
            color=MUTED,
        )
        assert self.page == 23, self.page
        self.canvas.save()
        print(f"Built {self.page} pages with {self.figure} illustrated figures.")


if __name__ == "__main__":
    audit = json.loads((EVIDENCE / "dependency-audit.json").read_text())
    assert len(audit["dependencies"]) == 61 and not any(
        d["vulns"] for d in audit["dependencies"]
    )
    assert "Ran 54 tests" in (EVIDENCE / "api-tests.txt").read_text()
    assert json.loads((EVIDENCE / "demo-results.json").read_text())["result"] == "PASS"
    Report().build()
