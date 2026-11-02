"""Build the 6 video slides (1920x1080 PNG) with PIL.

Dark background, white text, cyan accent. Title + bullets per slide.
Text is wrapped to fit the frame; long lines are split.
"""
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
BG = (10, 17, 32)          # dark navy
ACCENT = (34, 211, 238)    # cyan
TITLE_C = (255, 255, 255)
BODY_C = (203, 213, 225)   # slate-300
MUTED_C = (148, 163, 184)  # slate-400

FONT_TITLE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_BODY = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def wrap(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def slide(path, title, subtitle, bullets, footer=None, mono_lines=None):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # Accent bar top
    d.rectangle([0, 0, W, 8], fill=ACCENT)
    # Accent bar bottom
    d.rectangle([0, H - 8, W, H], fill=ACCENT)
    title_font = ImageFont.truetype(FONT_TITLE, 72)
    sub_font = ImageFont.truetype(FONT_BODY, 40)
    body_font = ImageFont.truetype(FONT_BODY, 38)
    mono_font = ImageFont.truetype(FONT_MONO, 34)
    footer_font = ImageFont.truetype(FONT_BODY, 30)

    x = 120
    y = 110
    for line in wrap(d, title, title_font, W - 2 * x):
        d.text((x, y), line, font=title_font, fill=TITLE_C)
        y += 92
    y += 10
    if subtitle:
        for line in wrap(d, subtitle, sub_font, W - 2 * x):
            d.text((x, y), line, font=sub_font, fill=ACCENT)
            y += 56
        y += 30
    for b in bullets:
        lines = wrap(d, b, body_font, W - 2 * x - 60)
        d.text((x, y + 6), "•", font=body_font, fill=ACCENT)
        for line in lines:
            d.text((x + 60, y), line, font=body_font, fill=BODY_C)
            y += 54
        y += 18
    if mono_lines:
        y += 10
        for line in mono_lines:
            d.text((x, y), line, font=mono_font, fill=MUTED_C)
            y += 48
    if footer:
        d.text((x, H - 90), footer, font=footer_font, fill=MUTED_C)
    img.save(path)
    print(f"wrote {path}")


slide(
    "slides/slide1.png",
    "Cyber CIV Recovery Engine",
    "Zero-PII Crypto & Banking Asset Sensing",
    [
        "Find and recover your money — without handing over your",
        "account, your login, or your private key.",
    ],
    footer="Meta Global AI Developer Hackathon  •  Live now",
    mono_lines=["https://1c07b87e19009c.lhr.life"],
)

slide(
    "slides/slide2.png",
    "The Problem",
    "People lose access every day.",
    [
        "A lost key. A hacked exchange. A locked account.",
        "Traditional recovery tools make it worse: they ask you to",
        "connect your account and paste your credentials.",
        "If the recovery service gets breached, your money is gone.",
        "Cyber CIV flips the model: nothing connects. Nothing logs in.",
    ],
)

slide(
    "slides/slide3.png",
    "Zero-PII, by Construction",
    "Zero retention. Read-only sensing.",
    [
        "Banking: paste an MT940 snippet. Only currency, balances,",
        "and velocity are extracted. The account and statement",
        "numbers are discarded at ingress — never stored, never returned.",
        "Crypto: give it an extended public key. It derives your",
        "addresses and checks balances on-chain.",
        "Your private key never leaves your hands.",
        "Ingest is read-only and idempotent: sensing can never mutate state.",
    ],
)

slide(
    "slides/slide4.png",
    "Pure-Python Cryptography",
    "Standard library only. No supply-chain risk.",
    [
        "Hand-rolled secp256k1: point addition, double-and-add",
        "multiplication, compressed key parsing.",
        "Modular inversion offloaded to Python's C-backed pow(a, -1, p).",
        "20-address wallet lookahead: 0.23 – 0.26 seconds.",
        "BIP32 derivation, Bech32 encoding, Esplora UTXO lookups.",
        "Verified against live addresses, down to the satoshi.",
    ],
)

slide(
    "slides/slide5.png",
    "Defense in Depth",
    "Hardened before the freeze. Proven, not claimed.",
    [
        "64 KB body cap  •  256-char signal cap  •  Rate limits:",
        "10 mutating and 60 read requests per minute, per IP.",
        "Five failed logins trigger a five-minute IP lockout.",
        "Dashboard retries: 15-second timeout, exponential backoff.",
        "167 of 167 tests pass. A pre-freeze audit caught and fixed",
        "two real bugs: Bech32 decode guardrails, MT940 currency order.",
    ],
)

slide(
    "slides/slide6.png",
    "Live Now",
    "Your money, sensed from a signal.",
    [
        "Full source and audit log on GitHub.",
        "Commit 073538b, GPG-signed.",
        "Cycle 731. Audit chain valid.",
    ],
    footer="Thank you.",
    mono_lines=[
        "https://1c07b87e19009c.lhr.life",
        "github.com/pehlivanovicparis-cloud/cyber-civ",
    ],
)
