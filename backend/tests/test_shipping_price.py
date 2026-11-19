"""The sample kit's shipping charge is always the real one (call 44 said "एक सौ उनसठ रुपये" = ₹159)."""
from __future__ import annotations

from app.defaults import DEFAULT_AGENT_SETTINGS
from app.services.sarvam import speakable
from app.services.shipping_price import fix_shipping_amount, kit_shipping_amount

KB = DEFAULT_AGENT_SETTINGS["knowledge_base"]


def test_amount_comes_from_the_knowledge_base():
    assert kit_shipping_amount(KB) == 199
    assert kit_shipping_amount("Free sample kit: the customer pays only ₹249 shipping.") == 249
    assert kit_shipping_amount("no amount written here") is None
    assert kit_shipping_amount(None) is None


def test_wrong_amounts_about_the_kit_become_the_real_one():
    cases = [
        # call 44, word for word
        ("क्या आप sample kit भी लेना चाहेंगे, ताकि quality चेक कर सकें? सिर्फ shipping charges एक सौ उनसठ रुपये लगेंगे।",
         "क्या आप sample kit भी लेना चाहेंगे, ताकि quality चेक कर सकें? सिर्फ shipping charges ₹199 लगेंगे।"),
        ("Sample kit free है, बस डेढ़ सौ रुपये shipping लगते हैं।", "Sample kit free है, बस ₹199 shipping लगते हैं।"),
        ("The sample kit is free, you only pay 159 rupees for shipping.",
         "The sample kit is free, you only pay ₹199 for shipping."),
        ("The kit is free, shipping is ₹159.", "The kit is free, shipping is ₹199."),
        ("Sample kit के लिए Rs. 99 shipping लगेगा।", "Sample kit के लिए ₹199 shipping लगेगा।"),
        ("shipping के लिए one fifty nine rupees लगते हैं।", "shipping के लिए ₹199 लगते हैं।"),
    ]
    for said, fixed in cases:
        assert fix_shipping_amount(said, 199) == fixed, said


def test_right_amounts_stay_right_and_sound_right():
    for said in ("Sample kit free है, बस one ninety nine rupees shipping charges लगते हैं।",
                 "सैंपल किट के लिए बस एक सौ निन्यानवे रुपये shipping लगते हैं।",
                 "Sample kit free है, बस ₹199 shipping लगते हैं।"):
        out = fix_shipping_amount(said, 199)
        assert "₹199" in out and "उनसठ" not in out, out
        # the voice says it the way the knowledge base does
        assert "one ninety nine" in speakable(out, dict(DEFAULT_AGENT_SETTINGS)), out


def test_the_topic_can_come_from_the_question_or_the_previous_sentence():
    said = "बस एक सौ उनसठ रुपये लगेंगे।"
    assert fix_shipping_amount(said, 199) == said                       # no kit / shipping anywhere: untouched
    assert fix_shipping_amount(said, 199, "sample kit कैसे मिलेगा?") == "बस ₹199 लगेंगे।"


def test_other_amounts_and_numbers_are_left_alone():
    for said in (
        "Sample kit में आपका दो रुपये per piece वाला budget भी team देख लेगी।",  # the customer's own per-piece figure
        "Sample kit के साथ पाँच हज़ार pouches की details भी भेज देंगे।",          # a quantity, not money
        "हमारा MOQ एक हज़ार pieces से start होता है।",                         # not about the kit
        "Sample kit free है।",
    ):
        assert fix_shipping_amount(said, 199) == said, said
    assert fix_shipping_amount("shipping एक सौ उनसठ रुपये", None) == "shipping एक सौ उनसठ रुपये"  # guard off
