"""Pure unit tests: phone parsing, sentence chunking, audio helpers, prompts."""
from app.services.audio import CallRecorder, pcm16_to_ulaw, ulaw_to_pcm16
from app.services.llm import SentenceChunker, strip_end_token
from app.services.phone import normalize_phone
from app.services.prompts import build_system_prompt, fill_template
from app.defaults import DEFAULT_AGENT_SETTINGS


def test_normalize_phone():
    ok = {
        "9876543210": "+919876543210", "09876543210": "+919876543210", "919876543210": "+919876543210",
        "+91 98765-43210": "+919876543210", "(+91) 98765 43210": "+919876543210", "9876543210.0": "+919876543210",
        "9.876543210e9": "+919876543210", "0091 9876543210": "+919876543210",
    }
    for raw, want in ok.items():
        assert normalize_phone(raw) == want, raw
    # international numbers only when explicitly allowed (toll-fraud guard)
    assert normalize_phone("+1 415 555 0100") is None
    assert normalize_phone("+1 415 555 0100", allow_international=True) == "+14155550100"
    for bad in ["", None, "12345", "98765000039", "5876543210", "9.88E+09", "+91 98765", "abc", "+9198765432101"]:
        assert normalize_phone(bad) is None, bad


def test_sentence_chunker_hindi_and_end_token():
    c = SentenceChunker()
    out = []
    for tok in ["नमस्ते जी", "। आप ", "कैसे हैं? ", "बहुत ", "धन्यवाद! [END", "_CALL]"]:
        out += c.feed(tok)
    tail = c.flush()
    assert out[:2] == ["नमस्ते जी।", "आप कैसे हैं?"]
    joined = " ".join(out + [tail])
    text, ended = strip_end_token(joined)
    assert ended and "END" not in text and text.endswith("धन्यवाद!")


def test_chunker_soft_split_long_clause():
    c = SentenceChunker(soft_limit=40)
    out = c.feed("हम stand-up pouches, center seal pouches, spout pouches और quad seal pouches बनाते हैं")
    assert out and out[0].endswith(",")


def test_mulaw_roundtrip_and_recorder(tmp_path):
    import struct
    pcm = b"".join(struct.pack("<h", v) for v in (0, 1000, -1000, 8000, -8000, 30000))
    back = ulaw_to_pcm16(pcm16_to_ulaw(pcm))
    vals = struct.unpack("<6h", back)
    assert all(abs(a - b) <= max(64, abs(a) * 0.06) for a, b in zip((0, 1000, -1000, 8000, -8000, 30000), vals))

    rec = CallRecorder(8000)
    rec.add_user(b"\x01\x00" * 800)      # 100 ms customer
    rec.add_agent(b"\x02\x00" * 1600)    # 200 ms agent, starts "now"
    rec.interrupt_agent()                # nothing played beyond now -> trimmed
    assert len(rec.agent) <= len(rec.user)
    secs = rec.save(tmp_path / "x.wav")
    assert 0.09 < secs < 0.3 and (tmp_path / "x.wav").read_bytes()[:4] == b"RIFF"


def test_prompt_templates():
    cfg = dict(DEFAULT_AGENT_SETTINGS)
    lead = {"name": "Rahul Sharma", "company": "Sharma Snacks"}
    line = fill_template(cfg["opening_line"], cfg, lead)
    assert line.startswith("नमस्ते Rahul जी!") and "PouchWale" in line and "{" not in line
    assert "Pooja" not in line  # no personal name by default
    assert fill_template("{lead_name_ji}", cfg, {"name": ""}) == "जी"
    sp = build_system_prompt(cfg, lead)
    assert "[END_CALL]" in sp and "Sharma Snacks" in sp and "feminine" in sp
    assert "NO personal name" in sp
    named = build_system_prompt({**cfg, "agent_name": "Riya"}, lead)
    assert "Your name is Riya." in named and "NO personal name" not in named
    assert fill_template("मैं {agent_name} बोल रही हूँ", cfg, lead) == "मैं बोल रही हूँ"
    for q in cfg["qualification_questions"]:
        assert q in sp


def test_end_call_marker_only_explicit():
    for yes in ["धन्यवाद! [END_CALL]", "Thanks [END CALL]", "bye END_CALL", "[end_call]"]:
        assert strip_end_token(yes)[1], yes
    for no in ["मैं details send call पर भेज दूँगी", "we will end call later", "SEND_CALL_LOG", "call end कर रहे हैं"]:
        assert not strip_end_token(no)[1], no
