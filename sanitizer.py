#!/usr/bin/env python3
"""PIF Sanitizer — Non-LLM prompt injection scanner.

Scans text for prompt injection patterns, suspicious Unicode, homoglyphs,
and encoded payloads. Returns sanitized text with redaction markers and
a findings report. No LLM or external dependencies — stdlib only.
"""

import re
import base64
import unicodedata
import json
import sys
from pathlib import Path

CRITICAL = "CRITICAL"
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"

# --- Pattern Definitions ---
# Each entry: (compiled_regex, category, severity)
# Built at module load for performance.

_RAW_PATTERNS = {
    CRITICAL: {
        "instruction_override": [
            r'\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+)?(?:previous|prior|above|earlier|other|your|the|my)\s+(?:instructions?|directions?|prompts?|rules?|guidelines?|constraints?|programming)\b',
            r'\b(?:do\s+not|don\'t|never)\s+follow\s+(?:(?:previous|prior|above|earlier|your|the)\s+)+(?:instructions?|directions?|rules?)\b',
            r'\bbypass\s+(?:(?:all|your|previous|prior|the|my)\s+)+(?:instructions?|programming|rules?|guidelines?)\b',
            r'\bnew\s+instructions?\s*:',
            r'\bupdated?\s+instructions?\s*:',
            r'\b(?:begin|start)\s+new\s+(?:session|conversation|instructions?)\b',
            r'\byour\s+(?:new|real|actual|true)\s+(?:instructions?|role|task|goal|purpose|objective)\s+(?:is|are)\b',
        ],
        "role_hijacking": [
            r'\byou\s+are\s+now\s+(?:a|an|the|my|our)\s+\w',
            r'\b(?:act|behave)\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:a\s+|an\s+|the\s+)\w',
            r'\bpretend\s+(?:you\s+are|to\s+be|you\'re)\b',
            r'\broleplay\s+as\b',
            r'\byour\s+new\s+persona\b',
            r'\bsimulate\s+(?:being\s+)?(?:a\s+|an\s+)\w',
            r'\bDAN\s+mode\b',
            r'\bdo\s+anything\s+now\b',
            r'\bdeveloper\s+mode\s+(?:enabled|activated|on)\b',
            r'\bgod\s+mode\b',
            r'\benable\s+(?:developer|debug|admin|root|unrestricted)\s+mode\b',
            r'\bbypass\s+(?:your\s+)?(?:safety|content|ethical)\s+(?:filters?|restrictions?|guidelines?)\b',
            r'\bwithout\s+(?:ethical|safety|content)\s+(?:constraints?|guidelines?|restrictions?|filters?)\b',
        ],
        "delimiter_injection": [
            r'<\|im_start\|>',
            r'<\|im_end\|>',
            r'<\|endoftext\|>',
            r'<\|system\|>',
            r'<\|user\|>',
            r'<\|assistant\|>',
            r'<\|pad\|>',
            r'<\|eos\|>',
            r'<\|bos\|>',
            r'\[INST\]',
            r'\[/INST\]',
            r'<<SYS>>',
            r'<</SYS>>',
        ],
        "system_prompt_extraction": [
            r'\b(?:reveal|show|display|print|output|repeat|echo|dump|leak)\s+(?:your\s+)?(?:full\s+)?(?:system\s+)?(?:prompt|instructions?|rules?|guidelines?|context|directives?)\b',
            r'\bwhat\s+(?:are|is|were)\s+your\s+(?:system\s+)?(?:prompt|instructions?|rules?|guidelines?|directives?)\b',
            r'\brepeat\s+(?:everything|all|the\s+text|verbatim)\s+(?:above|before|from\s+the\s+(?:beginning|start))\b',
        ],
    },
    HIGH: {
        "indirect_injection": [
            r'^\s*(?:ASSISTANT|SYSTEM|HUMAN|USER)\s*:',
            r'\[(?:Note|Instruction|Command|Message|Directive)\s+(?:to|for)\s+(?:the\s+)?(?:AI|assistant|model|LLM|Claude|GPT|chatbot)\s*[:\]]',
            r'\((?:Note|Instruction|Command|Message|Directive)\s+(?:to|for)\s+(?:the\s+)?(?:AI|assistant|model|LLM|Claude|GPT|chatbot)\s*[:\)]',
            r'<(?:instructions?)>',
            r'</(?:instructions?)>',
            r'\b(?:hey|attention|dear)\s+(?:AI|assistant|model|LLM|Claude|GPT|chatbot)\b',
            r'\bwhen\s+(?:the|an?)\s+(?:AI|assistant|model|LLM)\s+reads?\s+this\b',
        ],
        "data_exfiltration": [
            r'!\[[^\]]*\]\(https?://[^\s)]*\?[^\s)]*(?:data|payload|token|key|secret|password|exfil|steal|leak)=[^\s)]*\)',
            r'\bnavigator\.sendBeacon\s*\(',
            r'\bfetch\s*\(\s*["\']https?://',
            r'\bnew\s+XMLHttpRequest\b',
            r'\bdocument\.location\s*=\s*["\']https?://',
            r'\bwindow\.location\s*=\s*["\']https?://',
        ],
    },
    LOW: {
        "suspicious_html": [
            r'<!--[\s\S]{50,}?-->',
        ],
        "markdown_injection": [
            r'!\[[^\]]*\]\(javascript:',
            r'!\[[^\]]*\]\(data:text/',
        ],
    },
}

# Compile all patterns at module load
_COMPILED_PATTERNS = []
for severity, categories in _RAW_PATTERNS.items():
    for category, patterns in categories.items():
        for pattern in patterns:
            flags = re.IGNORECASE | re.MULTILINE
            _COMPILED_PATTERNS.append((
                re.compile(pattern, flags),
                category,
                severity,
            ))

# Suspicious Unicode — always stripped
_SUSPICIOUS_CHARS = {
    '​': 'zero-width space',
    '‌': 'zero-width non-joiner',
    '‍': 'zero-width joiner',
    '‎': 'left-to-right mark',
    '‏': 'right-to-left mark',
    '­': 'soft hyphen',
    '‪': 'left-to-right embedding',
    '‫': 'right-to-left embedding',
    '‬': 'pop directional formatting',
    '‭': 'left-to-right override',
    '‮': 'right-to-left override',
    '⁠': 'word joiner',
    '⁡': 'function application',
    '⁢': 'invisible times',
    '⁣': 'invisible separator',
    '⁤': 'invisible plus',
    '⁦': 'left-to-right isolate',
    '⁧': 'right-to-left isolate',
    '⁨': 'first strong isolate',
    '⁩': 'pop directional isolate',
    '﻿': 'byte order mark (mid-text)',
    '￹': 'interlinear annotation anchor',
    '￺': 'interlinear annotation separator',
    '￻': 'interlinear annotation terminator',
}

_SUSPICIOUS_CHARS_RE = re.compile(
    '[' + ''.join(re.escape(c) for c in _SUSPICIOUS_CHARS) + ']'
)

# Tag block: U+E0001 to U+E007F (invisible language tags, used for steganography)
_TAG_CHAR_RE = re.compile(r'[\U000e0001-\U000e007f]+')

# Homoglyph map — Cyrillic/Greek chars that look like Latin
_HOMOGLYPHS = {
    'А': 'A', 'а': 'a',  # Cyrillic А/а
    'В': 'B', 'в': 'b',  # Cyrillic В/в (actually looks like B/b)
    'С': 'C', 'с': 'c',  # Cyrillic С/с
    'Е': 'E', 'е': 'e',  # Cyrillic Е/е
    'Н': 'H', 'н': 'h',  # Cyrillic Н/н
    'К': 'K', 'к': 'k',  # Cyrillic К/к
    'М': 'M', 'м': 'm',  # Cyrillic М/м (visually similar)
    'О': 'O', 'о': 'o',  # Cyrillic О/о
    'Р': 'P', 'р': 'p',  # Cyrillic Р/р
    'Т': 'T', 'т': 't',  # Cyrillic Т/т (some fonts)
    'Х': 'X', 'х': 'x',  # Cyrillic Х/х
    'У': 'Y', 'у': 'y',  # Cyrillic У/у
    'Β': 'B', 'β': 'b',  # Greek Β/β
    'Ε': 'E', 'ε': 'e',  # Greek Ε/ε (capital only)
    'Η': 'H',                  # Greek Η
    'Κ': 'K', 'κ': 'k',  # Greek Κ/κ
    'Μ': 'M',                  # Greek Μ
    'Ν': 'N',                  # Greek Ν
    'Ο': 'O', 'ο': 'o',  # Greek Ο/ο
    'Ρ': 'P', 'ρ': 'p',  # Greek Ρ/ρ
    'Τ': 'T', 'τ': 't',  # Greek Τ/τ (capital only)
    'Χ': 'X', 'χ': 'x',  # Greek Χ/χ
    'Ζ': 'Z', 'ζ': 'z',  # Greek Ζ/ζ (capital only)
}
_HOMOGLYPH_RE = re.compile('[' + ''.join(re.escape(c) for c in _HOMOGLYPHS) + ']')

# Base64 detection — long runs of base64 alphabet
_BASE64_RE = re.compile(r'[A-Za-z0-9+/]{80,}={0,2}')

# Code fence detection
_CODE_FENCE_RE = re.compile(r'^(`{3,}|~{3,}).*$', re.MULTILINE)


def _find_code_blocks(text):
    """Return set of (start, end) ranges that are inside code fences."""
    blocks = []
    fences = list(_CODE_FENCE_RE.finditer(text))
    i = 0
    while i < len(fences) - 1:
        open_fence = fences[i]
        open_char = open_fence.group(1)[0]
        open_len = len(open_fence.group(1))
        for j in range(i + 1, len(fences)):
            close_fence = fences[j]
            close_char = close_fence.group(1)[0]
            close_len = len(close_fence.group(1))
            if close_char == open_char and close_len >= open_len:
                blocks.append((open_fence.start(), close_fence.end()))
                i = j + 1
                break
        else:
            i += 1
    return blocks


def _in_code_block(pos, code_blocks):
    """Check if a position falls inside any code block."""
    for start, end in code_blocks:
        if start <= pos < end:
            return True
    return False


def _snippet(text, match_start, match_end, context=30):
    """Extract a context snippet around a match."""
    start = max(0, match_start - context)
    end = min(len(text), match_end + context)
    s = text[start:end].replace('\n', ' ').strip()
    if start > 0:
        s = '...' + s
    if end < len(text):
        s = s + '...'
    return s


def sanitize(text, strict=False):
    """Scan text for prompt injection and return (clean_text, findings).

    Args:
        text: Raw text to scan.
        strict: If True, also check LOW severity patterns.

    Returns:
        (clean_text, findings) where findings is a list of dicts with keys:
        severity, category, reason, line, snippet.
    """
    findings = []
    clean = text

    # --- Phase 1: Strip suspicious Unicode ---
    unicode_count = 0
    stripped_chars = {}

    def _record_unicode(m):
        nonlocal unicode_count
        char = m.group(0)
        name = _SUSPICIOUS_CHARS.get(char, f'U+{ord(char):04X}')
        stripped_chars[name] = stripped_chars.get(name, 0) + 1
        unicode_count += 1
        return ''

    clean = _SUSPICIOUS_CHARS_RE.sub(_record_unicode, clean)

    # Strip tag block characters
    tag_matches = list(_TAG_CHAR_RE.finditer(clean))
    if tag_matches:
        tag_count = sum(len(m.group(0)) for m in tag_matches)
        unicode_count += tag_count
        stripped_chars['tag block (U+E0001-E007F)'] = tag_count
        clean = _TAG_CHAR_RE.sub('', clean)

    if unicode_count > 0:
        details = ', '.join(f'{v}x {k}' for k, v in stripped_chars.items())
        findings.append({
            'severity': MEDIUM,
            'category': 'unicode_attack',
            'reason': f'Stripped {unicode_count} suspicious Unicode characters: {details}',
            'line': 0,
            'snippet': '',
        })

    # --- Phase 2: Homoglyph detection + NFKC normalization ---
    homoglyph_count = 0
    replaced_chars = {}

    def _replace_homoglyph(m):
        nonlocal homoglyph_count
        char = m.group(0)
        latin = _HOMOGLYPHS[char]
        replaced_chars[char] = replaced_chars.get(char, 0) + 1
        homoglyph_count += 1
        return latin

    clean = _HOMOGLYPH_RE.sub(_replace_homoglyph, clean)
    if homoglyph_count > 0:
        details = ', '.join(
            f'{v}x {k}->{ _HOMOGLYPHS[k]}' for k, v in replaced_chars.items()
        )
        findings.append({
            'severity': MEDIUM,
            'category': 'homoglyph',
            'reason': f'Replaced {homoglyph_count} homoglyph characters: {details}',
            'line': 0,
            'snippet': '',
        })

    clean = unicodedata.normalize('NFKC', clean)

    # --- Phase 3: Pattern matching ---
    code_blocks = _find_code_blocks(clean)

    # Delimiter injection tokens are ALWAYS stripped, even in code blocks
    delimiter_patterns = [
        (p, cat, sev) for p, cat, sev in _COMPILED_PATTERNS
        if cat == 'delimiter_injection'
    ]
    other_patterns = [
        (p, cat, sev) for p, cat, sev in _COMPILED_PATTERNS
        if cat != 'delimiter_injection'
    ]

    # Filter by severity level
    active_severities = {CRITICAL, HIGH, MEDIUM}
    if strict:
        active_severities.add(LOW)

    # Process delimiter patterns (always, regardless of code blocks)
    for pattern, category, severity in delimiter_patterns:
        if severity not in active_severities:
            continue
        new_clean = clean
        for m in reversed(list(pattern.finditer(clean))):
            line_num = clean[:m.start()].count('\n') + 1
            findings.append({
                'severity': severity,
                'category': category,
                'reason': f'Matched: {m.group(0)!r}',
                'line': line_num,
                'snippet': _snippet(clean, m.start(), m.end()),
            })
            redaction = f'[REDACTED: {category}/{severity}]'
            new_clean = new_clean[:m.start()] + redaction + new_clean[m.end():]
        clean = new_clean

    # Process other patterns (skip matches inside code blocks)
    for pattern, category, severity in other_patterns:
        if severity not in active_severities:
            continue
        # Rebuild code blocks after each round of redactions may shift positions
        code_blocks = _find_code_blocks(clean)
        new_clean = clean
        offset = 0
        for m in list(pattern.finditer(clean)):
            if _in_code_block(m.start(), code_blocks):
                continue
            line_num = clean[:m.start()].count('\n') + 1
            findings.append({
                'severity': severity,
                'category': category,
                'reason': f'Matched: {m.group(0)!r}',
                'line': line_num,
                'snippet': _snippet(clean, m.start(), m.end()),
            })
            redaction = f'[REDACTED: {category}/{severity}]'
            new_clean = (
                new_clean[:m.start() + offset]
                + redaction
                + new_clean[m.end() + offset:]
            )
            offset += len(redaction) - (m.end() - m.start())
        clean = new_clean

    # --- Phase 4: Base64 payload detection ---
    code_blocks = _find_code_blocks(clean)
    for m in list(_BASE64_RE.finditer(clean)):
        if _in_code_block(m.start(), code_blocks):
            continue
        try:
            decoded = base64.b64decode(m.group(0), validate=True).decode('utf-8', errors='ignore')
        except Exception:
            continue
        # Re-scan decoded content for injection patterns
        _, sub_findings = sanitize(decoded, strict=strict)
        if any(f['severity'] in (CRITICAL, HIGH) for f in sub_findings):
            line_num = clean[:m.start()].count('\n') + 1
            findings.append({
                'severity': HIGH,
                'category': 'encoded_payload',
                'reason': f'Base64 payload decodes to injection: {decoded[:80]!r}',
                'line': line_num,
                'snippet': _snippet(clean, m.start(), m.end()),
            })
            redaction = '[REDACTED: encoded_payload/HIGH]'
            clean = clean[:m.start()] + redaction + clean[m.end():]

    # Sort findings by severity
    severity_order = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}
    findings.sort(key=lambda f: severity_order.get(f['severity'], 99))

    return clean, findings


def generate_report(url, raw_text, clean_text, findings):
    """Generate a JSON-serializable report dict."""
    return {
        'url': url,
        'raw_size_bytes': len(raw_text.encode('utf-8')),
        'clean_size_bytes': len(clean_text.encode('utf-8')),
        'findings': findings,
        'total_findings': len(findings),
        'critical_count': sum(1 for f in findings if f['severity'] == CRITICAL),
        'high_count': sum(1 for f in findings if f['severity'] == HIGH),
        'medium_count': sum(1 for f in findings if f['severity'] == MEDIUM),
        'low_count': sum(1 for f in findings if f['severity'] == LOW),
    }


# --- CLI entry point ---
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <input_file> [clean_file] [report_file] [--strict]', file=sys.stderr)
        sys.exit(1)

    strict = '--strict' in sys.argv
    args = [a for a in sys.argv[1:] if a != '--strict']

    input_path = Path(args[0])
    clean_path = Path(args[1]) if len(args) > 1 else input_path.with_suffix('.clean.txt')
    report_path = Path(args[2]) if len(args) > 2 else input_path.with_suffix('.report.json')

    raw_text = input_path.read_text(encoding='utf-8', errors='replace')
    clean_text, findings = sanitize(raw_text, strict=strict)

    clean_path.write_text(clean_text, encoding='utf-8')
    report = generate_report(str(input_path), raw_text, clean_text, findings)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')

    severity_counts = {CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0}
    for f in findings:
        severity_counts[f['severity']] = severity_counts.get(f['severity'], 0) + 1

    summary_parts = []
    for sev in [CRITICAL, HIGH, MEDIUM, LOW]:
        if severity_counts[sev]:
            summary_parts.append(f'{severity_counts[sev]} {sev}')

    if summary_parts:
        print(f'PIF: {", ".join(summary_parts)} findings')
    else:
        print('PIF: clean')
    print(f'Clean:  {clean_path}')
    print(f'Report: {report_path}')
