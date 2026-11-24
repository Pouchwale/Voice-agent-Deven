"""English words in a Hindi sentence, written in Devanagari for a Hindi voice.

A Hindi voice (Sarvam's `name_hi_style` personas) reads an English word written in English letters in a different
voice: the user heard her "change tone" on words like "pieces", "start", "quality" (call 74, 2026-11-24). Measured on
Sarvam's v4 voices the same day: on an English-heavy line her pitch moved twice as far from the all-Devanagari reading
as between two takes of the same text (Sarika 2.0 vs 0.9 semitones, Aparna 2.2 vs 1.3); Sarvam's Hinglish (`enhi`)
voices did not move, and they are meant to get English in English letters, so they are left alone.

Only the audio uses this (speakable()); transcripts keep the English spelling. Words not in the list stay as they are
(names mostly), an unknown all-capitals short word is spelled out letter by letter ("FSSAI" -> "एफ़ एस एस ए आई"), and a
sentence with no Hindi in it (a one-off English reply) is left alone.
"""
from __future__ import annotations

import re
import unicodedata

# The English words she says in Hindi sentences (from 442 Hindi replies on real calls, plus the packaging words in our
# knowledge), the way a Hindi speaker says them.
WORDS: dict[str, str] = {
    # pouches and packaging
    "pouch": "पाउच", "pouches": "पाउचेस", "packaging": "पैकेजिंग", "packing": "पैकिंग", "package": "पैकेज",
    "packet": "पैकेट", "packets": "पैकेट्स", "seal": "सील", "sealing": "सीलिंग", "side": "साइड", "center": "सेंटर",
    "centre": "सेंटर", "stand": "स्टैंड", "up": "अप", "standup": "स्टैंडअप", "quad": "क्वाड", "spout": "स्पाउट",
    "zipper": "ज़िपर", "zip": "ज़िप", "lock": "लॉक", "window": "विंडो", "matte": "मैट", "matt": "मैट", "gloss": "ग्लॉस",
    "glossy": "ग्लॉसी", "finish": "फ़िनिश", "tear": "टियर", "notch": "नॉच", "hang": "हैंग", "hole": "होल", "valve": "वाल्व",
    "kraft": "क्राफ़्ट", "paper": "पेपर", "foil": "फ़ॉइल", "metallised": "मेटलाइज़्ड", "metallized": "मेटलाइज़्ड",
    "film": "फ़िल्म", "films": "फ़िल्म्स", "layer": "लेयर", "layers": "लेयर्स", "laminated": "लैमिनेटेड",
    "lamination": "लैमिनेशन", "plastic": "प्लास्टिक", "material": "मटीरियल", "materials": "मटीरियल्स",
    "barrier": "बैरियर", "vacuum": "वैक्यूम", "freeze": "फ़्रीज़", "grade": "ग्रेड", "food": "फ़ूड", "airtight": "एयरटाइट",
    "moisture": "मॉइस्चर", "proof": "प्रूफ़", "oxygen": "ऑक्सीजन", "aroma": "अरोमा", "fresh": "फ़्रेश",
    "freshness": "फ़्रेशनेस", "shelf": "शेल्फ़", "life": "लाइफ़", "custom": "कस्टम", "printed": "प्रिंटेड", "print": "प्रिंट",
    "prints": "प्रिंट्स", "printing": "प्रिंटिंग", "digital": "डिजिटल", "rotogravure": "रोटोग्रेव्योर",
    "gravure": "ग्रेव्योर", "flexographic": "फ़्लेक्सोग्राफ़िक", "design": "डिज़ाइन", "designs": "डिज़ाइन्स",
    "branding": "ब्रांडिंग", "brand": "ब्रांड", "brands": "ब्रांड्स", "logo": "लोगो", "label": "लेबल", "labels": "लेबल्स",
    "barcode": "बारकोड", "artwork": "आर्टवर्क", "colour": "कलर", "color": "कलर", "colours": "कलर्स", "colors": "कलर्स",
    "vibrant": "वाइब्रेंट", "size": "साइज़", "sizes": "साइज़ेस", "shape": "शेप", "special": "स्पेशल", "flat": "फ़्लैट",
    "bottom": "बॉटम", "sachet": "सैशे", "sachets": "सैशे", "bag": "बैग", "bags": "बैग्स", "box": "बॉक्स",
    "gram": "ग्राम", "grams": "ग्राम", "kg": "किलो", "micron": "माइक्रॉन", "cylinder": "सिलेंडर", "plate": "प्लेट",
    "eco": "इको", "friendly": "फ़्रेंडली", "recyclable": "रिसाइकलेबल", "compostable": "कम्पोस्टेबल",
    "biodegradable": "बायोडिग्रेडेबल", "variety": "वैरायटी", "flavors": "फ़्लेवर्स", "flavours": "फ़्लेवर्स",
    # samples, price and orders
    "sample": "सैंपल", "samples": "सैंपल्स", "kit": "किट", "free": "फ़्री", "shipping": "शिपिंग", "charges": "चार्जेस",
    "charge": "चार्ज", "rupees": "रुपीज़", "one": "वन", "two": "टू", "three": "थ्री", "nine": "नाइन", "ten": "टेन",
    "ninety": "नाइंटी", "hundred": "हंड्रेड", "thousand": "थाउज़ेंड", "order": "ऑर्डर", "orders": "ऑर्डर्स",
    "quantity": "क्वांटिटी", "quantities": "क्वांटिटीज़", "moq": "एम ओ क्यू", "minimum": "मिनिमम", "piece": "पीस",
    "pieces": "पीसेस", "per": "पर", "rate": "रेट", "price": "प्राइस", "pricing": "प्राइसिंग", "cost": "कॉस्ट",
    "costing": "कॉस्टिंग", "quote": "कोट", "quotes": "कोट्स", "quotation": "कोटेशन", "discount": "डिस्काउंट",
    "exact": "एग्ज़ैक्ट", "exactly": "एग्ज़ैक्टली", "quality": "क्वालिटी", "high": "हाई", "premium": "प्रीमियम",
    "delivery": "डिलीवरी", "dispatch": "डिस्पैच", "turnaround": "टर्नअराउंड", "fast": "फ़ास्ट", "low": "लो",
    "small": "स्मॉल", "bulk": "बल्क", "trial": "ट्रायल", "repeat": "रिपीट", "payment": "पेमेंट", "advance": "एडवांस",
    "credit": "क्रेडिट", "bill": "बिल", "invoice": "इनवॉइस", "courier": "कूरियर", "transport": "ट्रांसपोर्ट",
    "budget": "बजट", "total": "टोटल", "range": "रेंज", "extra": "एक्स्ट्रा", "urgent": "अर्जेंट",
    "effective": "इफ़ेक्टिव", "reasonable": "रीज़नेबल", "manageable": "मैनेजेबल", "basis": "बेसिस",
    # the company and the call
    "production": "प्रोडक्शन", "machine": "मशीन", "machines": "मशीन्स", "machinery": "मशीनरी", "plant": "प्लांट",
    "factory": "फ़ैक्ट्री", "office": "ऑफ़िस", "location": "लोकेशन", "address": "एड्रेस", "visit": "विज़िट",
    "team": "टीम", "expert": "एक्सपर्ट", "experts": "एक्सपर्ट्स", "sales": "सेल्स", "company": "कंपनी",
    "business": "बिज़नेस", "startup": "स्टार्टअप", "customer": "कस्टमर", "customers": "कस्टमर्स", "supplier": "सप्लायर",
    "suppliers": "सप्लायर्स", "vendor": "वेंडर", "market": "मार्केट", "retail": "रिटेल", "online": "ऑनलाइन",
    "export": "एक्सपोर्ट", "local": "लोकल", "product": "प्रोडक्ट", "products": "प्रोडक्ट्स", "variant": "वेरिएंट",
    "variants": "वेरिएंट्स", "sku": "एस के यू", "skus": "एस के यूज़", "type": "टाइप", "types": "टाइप्स",
    "category": "कैटेगरी", "option": "ऑप्शन", "options": "ऑप्शन्स", "detail": "डिटेल", "details": "डिटेल्स",
    "whatsapp": "व्हाट्सऐप", "email": "ईमेल", "message": "मैसेज", "link": "लिंक", "website": "वेबसाइट", "call": "कॉल",
    "calls": "कॉल्स", "back": "बैक", "phone": "फ़ोन", "number": "नंबर", "contact": "कॉन्टैक्ट", "line": "लाइन",
    "cut": "कट", "drop": "ड्रॉप", "voice": "वॉइस", "assistant": "असिस्टेंट", "schedule": "शेड्यूल", "time": "टाइम",
    "second": "सेकंड", "seconds": "सेकंड्स", "minute": "मिनट", "minutes": "मिनट्स", "month": "मंथ", "monthly": "मंथली",
    "day": "डे", "days": "डेज़", "today": "टुडे", "tomorrow": "टुमॉरो", "pm": "पी एम", "gmp": "जी एम पी",
    "iso": "आई एस ओ", "gst": "जी एस टी", "mrp": "एम आर पी", "fssai": "एफ़ एस एस ए आई", "gsm": "जी एस एम",
    "certified": "सर्टिफ़ाइड", "certificate": "सर्टिफ़िकेट", "certifications": "सर्टिफ़िकेशन्स",
    "gujarat": "गुजरात", "mehsana": "मेहसाणा", "wale": "वाले",
    # things she does
    "confirm": "कन्फ़र्म", "confirmed": "कन्फ़र्म्ड", "arrange": "अरेंज", "finalize": "फ़ाइनलाइज़",
    "finalise": "फ़ाइनलाइज़", "final": "फ़ाइनल", "discuss": "डिस्कस", "share": "शेयर", "send": "सेंड", "help": "हेल्प",
    "suggest": "सजेस्ट", "suggestion": "सजेशन", "recommend": "रिकमेंड", "prefer": "प्रिफ़र", "choose": "चूज़",
    "choice": "चॉइस", "selection": "सिलेक्शन", "decide": "डिसाइड", "decided": "डिसाइडेड", "decision": "डिसीज़न",
    "clear": "क्लियर", "clarify": "क्लैरिफ़ाई", "check": "चेक", "note": "नोट", "test": "टेस्ट", "request": "रिक्वेस्ट",
    "requirement": "रिक्वायरमेंट", "requirements": "रिक्वायरमेंट्स", "need": "नीड", "needs": "नीड्स", "use": "यूज़",
    "using": "यूज़िंग", "start": "स्टार्ट", "end": "एंड", "depend": "डिपेंड", "compare": "कम्पेयर",
    "compared": "कम्पेयर्ड", "consider": "कंसीडर", "maintain": "मेंटेन", "offer": "ऑफ़र", "connect": "कनेक्ट",
    "explain": "एक्सप्लेन", "understand": "अंडरस्टैंड", "process": "प्रोसेस", "step": "स्टेप", "steps": "स्टेप्स",
    "approval": "अप्रूवल", "approve": "अप्रूव", "disturb": "डिस्टर्ब", "disturbance": "डिस्टर्बेंस", "report": "रिपोर्ट",
    "mix": "मिक्स", "combined": "कंबाइंड", "shift": "शिफ़्ट",
    # how she describes things
    "ready": "रेडी", "available": "अवेलेबल", "possible": "पॉसिबल", "convenient": "कन्वीनिएंट",
    "convenience": "कन्वीनिएंस", "flexible": "फ़्लेक्सिबल", "flexibility": "फ़्लेक्सिबिलिटी", "specific": "स्पेसिफ़िक",
    "particular": "पर्टिकुलर", "specifications": "स्पेसिफ़िकेशन्स", "basic": "बेसिक", "perfect": "परफ़ेक्ट",
    "ideal": "आइडियल", "best": "बेस्ट", "good": "गुड", "great": "ग्रेट", "nice": "नाइस", "popular": "पॉपुलर",
    "modern": "मॉडर्न", "traditional": "ट्रेडिशनल", "attractive": "अट्रैक्टिव", "standout": "स्टैंडआउट",
    "sharp": "शार्प", "detailed": "डिटेल्ड", "latest": "लेटेस्ट", "practical": "प्रैक्टिकल", "different": "डिफ़रेंट",
    "multiple": "मल्टिपल", "suitable": "सूटेबल", "efficiently": "इफ़िशिएंटली", "generally": "जनरली",
    "usually": "यूज़ुअली", "long": "लॉन्ग", "term": "टर्म", "experienced": "एक्सपीरियंस्ड", "images": "इमेजेस",
    "resolution": "रेज़ोल्यूशन", "medium": "मीडियम", "runs": "रन्स", "capabilities": "कैपेबिलिटीज़",
    "factors": "फ़ैक्टर्स", "complexity": "कॉम्प्लेक्सिटी", "model": "मॉडल", "idea": "आइडिया", "problem": "प्रॉब्लम",
    "guidance": "गाइडेंस", "urgency": "अर्जेंसी", "interested": "इंटरेस्टेड", "public": "पब्लिक", "current": "करंट",
    "currently": "करंटली", "already": "ऑलरेडी", "actually": "एक्चुअली", "basically": "बेसिकली", "around": "अराउंड",
    "approx": "अप्रॉक्स", "approximately": "अप्रॉक्सिमेटली", "standard": "स्टैंडर्ड", "normal": "नॉर्मल",
    "lead": "लीड", "conversation": "कन्वर्सेशन", "enthusiasm": "एंथूज़ियाज़्म", "respond": "रिस्पॉन्ड",
    "assist": "असिस्ट", "visually": "विज़ुअली", "busy": "बिज़ी", "situated": "सिचुएटेड",
    # products customers make
    "dry": "ड्राई", "fruits": "फ़्रूट्स", "fruit": "फ़्रूट", "namkeen": "नमकीन", "spices": "स्पाइसेस", "masala": "मसाला",
    "snacks": "स्नैक्स", "chips": "चिप्स", "sauces": "सॉसेस", "sauce": "सॉस", "ketchup": "केचप", "coffee": "कॉफ़ी",
    "tea": "टी", "rice": "राइस", "nuts": "नट्स", "beverages": "बेवरेजेस", "medicines": "मेडिसिन्स", "milk": "मिल्क",
    "powder": "पाउडर", "chocolates": "चॉकलेट्स", "chocolate": "चॉकलेट", "candy": "कैंडी", "grains": "ग्रेन्स",
    "paneer": "पनीर", "dairy": "डेयरी", "frozen": "फ़्रोज़न", "foods": "फ़ूड्स", "nutritional": "न्यूट्रिशनल",
    "supplements": "सप्लीमेंट्स", "protein": "प्रोटीन", "cosmetics": "कॉस्मेटिक्स", "pet": "पेट", "seeds": "सीड्स",
    "eat": "ईट", "meals": "मील्स", "liquid": "लिक्विड", "power": "पावर", "potato": "पोटैटो", "wafers": "वेफ़र्स",
    "oats": "ओट्स", "cereals": "सीरियल्स", "bakery": "बेकरी", "biscuits": "बिस्किट्स", "cookies": "कुकीज़",
    "popcorn": "पॉपकॉर्न", "pickle": "पिकल", "juice": "जूस", "oil": "ऑयल", "honey": "हनी", "ghee": "घी",
    "detergent": "डिटर्जेंट", "soap": "सोप", "shampoo": "शैम्पू", "seasonings": "सीज़निंग्स", "sugar": "शुगर",
    # small words of polite talk
    "thank": "थैंक", "thanks": "थैंक्स", "you": "यू", "please": "प्लीज़", "sorry": "सॉरी", "hello": "हेलो",
    "welcome": "वेलकम", "okay": "ओके", "ok": "ओके", "sure": "श्योर", "right": "राइट", "yes": "येस", "no": "नो",
    "sir": "सर", "madam": "मैडम", "ma'am": "मैम", "maam": "मैम", "a": "अ", "i": "आई", "it": "इट", "got": "गॉट",
    "for": "फ़ॉर", "of": "ऑफ़", "to": "टू", "with": "विद", "the": "द", "and": "एंड", "what": "व्हाट", "how": "हाउ",
    "can": "कैन", "have": "हैव", "are": "आर", "you're": "यू आर", "tell": "टेल", "me": "मी", "looking": "लुकिंग",
    "gp": "जी पी", "ai": "ए आई",
}
_LETTERS = {"A": "ए", "B": "बी", "C": "सी", "D": "डी", "E": "ई", "F": "एफ़", "G": "जी", "H": "एच", "I": "आई", "J": "जे",
            "K": "के", "L": "एल", "M": "एम", "N": "एन", "O": "ओ", "P": "पी", "Q": "क्यू", "R": "आर", "S": "एस", "T": "टी",
            "U": "यू", "V": "वी", "W": "डब्ल्यू", "X": "एक्स", "Y": "वाई", "Z": "ज़ेड"}
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*(?:-[A-Za-z]+)*")


def has_hindi(text: str) -> bool:
    return any(unicodedata.name(ch, "").startswith("DEVANAGARI LETTER") for ch in text)


def _one(m: re.Match) -> str:
    word = m.group(0)
    key = word.lower().replace("’", "'")
    if key in WORDS:
        return WORDS[key]
    if "-" in key and all(p in WORDS for p in key.split("-")):  # "stand-up", "high-quality", "per-piece"
        return "-".join(WORDS[p] for p in key.split("-"))
    if word.isupper() and 2 <= len(word) <= 5 and word.isalpha():  # "FSSAI", "UPI": spelled out
        return " ".join(_LETTERS[c] for c in word)
    return word


def english_in_devanagari(text: str) -> str:
    """"MOQ एक हज़ार pieces से start होता है" -> "एम ओ क्यू एक हज़ार पीसेस से स्टार्ट होता है". A sentence with no Hindi in it is
    returned unchanged (a one-off English reply is read as English)."""
    if not text or not has_hindi(text):
        return text
    return _LATIN_WORD.sub(_one, text)
