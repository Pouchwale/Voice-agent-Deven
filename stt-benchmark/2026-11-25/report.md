# Speech-recognition benchmark

Method: see the docstring of `backend/tools/stt_benchmark.py`.
WER = word error rate (lower is better); key terms = share of key words heard right (higher is better).

| setup | condition | WER % | key terms % | clips |
|---|---|---|---|---|
| current | clean | 4.5 | 100.0 | 20 |
| current | traffic 10 dB | 6.0 | 97.6 | 20 |
| current | traffic 5 dB | 6.0 | 97.6 | 20 |
| current | voices 10 dB | 14.3 | 87.8 | 20 |
| current | voices 5 dB | 33.1 | 73.2 | 20 |
| current | all | 12.8 | 91.2 | 100 |
| v4 | clean | 3.0 | 100.0 | 20 |
| v4 | traffic 10 dB | 3.0 | 100.0 | 20 |
| v4 | traffic 5 dB | 3.0 | 100.0 | 20 |
| v4 | voices 10 dB | 9.0 | 92.7 | 20 |
| v4 | voices 5 dB | 29.3 | 82.9 | 20 |
| v4 | all | 9.5 | 95.1 | 100 |
| v4+terms | clean | 0.8 | 100.0 | 20 |
| v4+terms | traffic 10 dB | 1.5 | 97.6 | 20 |
| v4+terms | traffic 5 dB | 1.5 | 97.6 | 20 |
| v4+terms | voices 10 dB | 13.5 | 92.7 | 20 |
| v4+terms | voices 5 dB | 30.1 | 90.2 | 20 |
| v4+terms | all | 9.5 | 95.6 | 100 |

## What each setup heard

**00 (rahul)** truth: हाँ जी बोलिए
- current / clean: हाँ जी बोलिए।
- current / traffic 5 dB: हां जी। बोलिए।
- current / voices 5 dB: बोलिए
- v4 / clean: हाँ जी। बोलिए।
- v4 / traffic 5 dB: हाँ जी, बोलिए।
- v4 / voices 5 dB: हमारी company का नाम The Holy League है।
- v4+terms / clean: हाँ जी। बोलिए।
- v4+terms / traffic 5 dB: हाँ जी, बोलिए।
- v4+terms / voices 5 dB: हमारी company का नाम The Holy Name है।

**01 (priya)** truth: हाँ जी
- current / clean: हाँ जी
- current / traffic 5 dB: हां जी
- current / voices 5 dB: नहीं है हां जी
- v4 / clean: हाँ जी।
- v4 / traffic 5 dB: हाँ जी।
- v4 / voices 5 dB: नहीं है।
- v4+terms / clean: हाँ जी।
- v4+terms / traffic 5 dB: हाँ जी।
- v4+terms / voices 5 dB: नहीं है। हां जी, क्या?

**02 (amit)** truth: आपका MOQ कितना है?
- current / clean: आपका MOQ कितना है?
- current / traffic 5 dB: आपका MOQ कितना है?
- current / voices 5 dB: आपका MQ कितना है
- v4 / clean: आपका MOQ कितना है?
- v4 / traffic 5 dB: आपका MOQ कितना है?
- v4 / voices 5 dB: आपका MOQ कितना है? Call कीजिए।
- v4+terms / clean: आपका MOQ कितना है?
- v4+terms / traffic 5 dB: आपका MOQ कितना है?
- v4+terms / voices 5 dB: आपका MOQ कितना है? call कीजिए।

**03 (aarti_hi_customer)** truth: मुझे makhana के लिए standup pouch चाहिए
- current / clean: मुझे मखाना के लिए stand up pouch चाहिए
- current / traffic 5 dB: मुझे मखाना के लिए stand up pouch चाहिए।
- current / voices 5 dB: मुझे मखाना के लिए stand up pouch चाहिए
- v4 / clean: मुझे मखाना के लिए stand up pouch चाहिए।
- v4 / traffic 5 dB: मुझे मखाना के लिए stand-up pouch चाहिए।
- v4 / voices 5 dB: मुझे मखाना के लिए stand up pouch चाहिए।
- v4+terms / clean: मुझे मखाना के लिए standup pouch चाहिए।
- v4+terms / traffic 5 dB: मुझे मखाना के लिए standup pouch चाहिए।
- v4+terms / voices 5 dB: मुझे मखाना के लिए standup pouch चाहिए।

**04 (rahul)** truth: हमें हर महीने एक लाख pieces चाहिए
- current / clean: हमें हर महीने एक लाख pieces चाहिए।
- current / traffic 5 dB: हमें हर महीने 100000 पीसेस चाहिए।
- current / voices 5 dB: हमें हर महीने 1,00,000 pieces चाहिए।
- v4 / clean: हमें हर महीने 1,00,000 pieces चाहिए।
- v4 / traffic 5 dB: हमें हर महीने 1,00,000 pieces चाहिए।
- v4 / voices 5 dB: हमें हर महीने 1,00,000 pieces चाहिए।
- v4+terms / clean: हमें हर महीने 1,00,000 pieces चाहिए।
- v4+terms / traffic 5 dB: हमें हर महीने 1,00,000 pieces चाहिए।
- v4+terms / voices 5 dB: हमें हर महीने 1,00,000 pieces चाहिए।

**05 (priya)** truth: हमारी company का नाम The Holy Nutty है
- current / clean: हमारी कंपनी का नाम The Holy Nutty है।
- current / traffic 5 dB: हमारी कंपनी का नाम The Holy Nutty है।
- current / voices 5 dB: हमारी company का नाम The Holy Nutty है
- v4 / clean: हमारी company का नाम The Holy Nutty है।
- v4 / traffic 5 dB: हमारी company का नाम The Holy Nutty है।
- v4 / voices 5 dB: हमारी company का नाम The Holy Nutty है।
- v4+terms / clean: हमारी company का नाम The Holy Nutty है।
- v4+terms / traffic 5 dB: हमारी company का नाम The Holy Nutty है।
- v4+terms / voices 5 dB: हमारी company का नाम The Holy Nutty है।

**06 (amit)** truth: क्या आप zipper pouch भी बनाते हैं?
- current / clean: क्या आप zipper pouch भी बनाते हैं
- current / traffic 5 dB: क्या आप zipper pouch भी बनाते हैं?
- current / voices 5 dB: क्या आप zipper pouch भी बनाते हैं?
- v4 / clean: क्या आप zipper pouch भी बनाते हैं?
- v4 / traffic 5 dB: क्या आप zipper pouch भी बनाते हैं?
- v4 / voices 5 dB: क्या आप zipper pouch भी बनाते हैं?
- v4+terms / clean: क्या आप zipper pouch भी बनाते हैं?
- v4+terms / traffic 5 dB: क्या आप zipper pouch भी बनाते हैं?
- v4+terms / voices 5 dB: क्या आप zipper pouch भी बनाते हैं?

**07 (aarti_hi_customer)** truth: हमें दस हज़ार pouch चाहिए दो हफ्ते में
- current / clean: हमें 10,000 pouch चाहिए 2 हफ़्ते में
- current / traffic 5 dB: हमें 10000 पाउच चाहिए 2 हफ्ते में।
- current / voices 5 dB: नहीं अभी हमें ₹10000 पाउच चाहिए दो हफ्ते में
- v4 / clean: हमें 10,000 pouch चाहिए 2 हफ़्ते में।
- v4 / traffic 5 dB: हमें 10,000 pouch चाहिए 2 हफ़्ते में।
- v4 / voices 5 dB: हमें 10,000 pouch चाहिए 2 हफ्ते में।
- v4+terms / clean: हमें 10,000 pouch चाहिए 2 हफ्ते में।
- v4+terms / traffic 5 dB: हमें 10,000 pouch चाहिए 2 हफ़्ते में।
- v4+terms / voices 5 dB: हमें 10,000 pouch चाहिए 2 हफ्ते में।

**08 (rahul)** truth: sample kit मंगवाना है, WhatsApp पर भेज दीजिए
- current / clean: sample kit मंगवाना है WhatsApp पर भेज दीजिए।
- current / traffic 5 dB: सैंपल किट मंगवाना है, WhatsApp पर भेज दीजिए।
- current / voices 5 dB: शाम को temple kit मंगवाना है WhatsApp पर भेज दीजिए
- v4 / clean: Sample kit मंगवाना है। WhatsApp पर भेज दीजिए।
- v4 / traffic 5 dB: Sample kit मंगवाना है, WhatsApp पर भेज दीजिए।
- v4 / voices 5 dB: sample kit मंगवाना है। WhatsApp पर भेज दीजिए।
- v4+terms / clean: sample kit मंगवाना है। WhatsApp पर भेज दीजिए।
- v4+terms / traffic 5 dB: sample kit मंगवाना है। WhatsApp पर भेज दीजिए।
- v4+terms / voices 5 dB: sample kit मंगवाना है। WhatsApp पर भेज दीजिए।

**09 (priya)** truth: matte finish अच्छा रहेगा या gloss?
- current / clean: matt finish अच्छा रहेगा या gloss
- current / traffic 5 dB: मैट finish अच्छा रहेगा या ग्लॉस
- current / voices 5 dB: अच्छा रहेगा या gloss
- v4 / clean: Matte finish अच्छा रहेगा या gloss?
- v4 / traffic 5 dB: Matte finish अच्छा रहेगा या gloss?
- v4 / voices 5 dB: Matte finish अच्छा रहेगा या gloss?
- v4+terms / clean: matte finish अच्छा रहेगा या gloss?
- v4+terms / traffic 5 dB: matte finish अच्छा रहेगा या gloss?
- v4+terms / voices 5 dB: matte finish अच्छा रहेगा या gloss?

**10 (amit)** truth: मेरा namkeen का business है, center seal pouch चाहिए
- current / clean: मेरा नमकीना business है center seal pouch चाहिए
- current / traffic 5 dB: मेरा नमकीन का business है, center seal pouch चाहिए।
- current / voices 5 dB: हाँ मेरा नमकीना business है center seal pouch चाहिए हम
- v4 / clean: मेरा नमकीन का business है। Center seal pouch चाहिए।
- v4 / traffic 5 dB: मेरा नमकीन का business है, center seal pouch चाहिए।
- v4 / voices 5 dB: मेरा नमकीन का business है। Center seal pouch चाहिए। हम
- v4+terms / clean: मेरा नमकीन का business है। center seal pouch चाहिए।
- v4+terms / traffic 5 dB: मेरा नमकीन का business है। center seal pouch चाहिए।
- v4+terms / voices 5 dB: हाँ मेरा नमकीन का business है। center seal pouch चाहिए।

**11 (aarti_hi_customer)** truth: अभी हम spices के लिए किसी और company से packaging लेते हैं
- current / clean: अभी हम spices के लिए किसी और company से packaging लेते हैं
- current / traffic 5 dB: अभी हम spices के लिए किसी और कंपनी से packaging लेते हैं।
- current / voices 5 dB: आपका M के लिए किसी और company से packaging लेते हैं। आप लोग कहां से
- v4 / clean: अभी हम spices के लिए किसी और company से packaging लेते हैं।
- v4 / traffic 5 dB: अभी हम spices के लिए किसी और company से packaging लेते हैं।
- v4 / voices 5 dB: अभी हम Spical के लिए किसी और company से packaging लेते हैं। आप लोग कहाँ से हैं?
- v4+terms / clean: अभी हम spices के लिए किसी और company से packaging लेते हैं।
- v4+terms / traffic 5 dB: अभी हम spices के लिए किसी और company से packaging लेते हैं।
- v4+terms / voices 5 dB: अभी हम spout pouch के लिए किसी और company से packaging लेते हैं। आप लोग कहाँ से हैं?

**12 (rahul)** truth: नहीं, अभी design ready नहीं है
- current / clean: नहीं अभी design ready नहीं है
- current / traffic 5 dB: नहीं अभी design ready नहीं है।
- current / voices 5 dB: नहीं अभी design ready नहीं है
- v4 / clean: नहीं अभी design ready नहीं है।
- v4 / traffic 5 dB: नहीं, अभी design ready नहीं है।
- v4 / voices 5 dB: नहीं, अभी design ready नहीं है।
- v4+terms / clean: नहीं अभी design ready नहीं है।
- v4+terms / traffic 5 dB: नहीं, अभी design ready नहीं है।
- v4+terms / voices 5 dB: नहीं, अभी design ready नहीं है।

**13 (priya)** truth: शाम को पाँच बजे call कीजिए
- current / clean: शाम को पाँच बजे call कीजिए
- current / traffic 5 dB: शाम को 5:00 बजे कॉल कीजिए।
- current / voices 5 dB: शाम को 5 बजे call कीजिए
- v4 / clean: शाम को 5 बजे call कीजिए।
- v4 / traffic 5 dB: शाम को 5 बजे call कीजिए।
- v4 / voices 5 dB: शाम को 5 बजे call में कितना खर्चा आएगा?
- v4+terms / clean: शाम को 5 बजे call कीजिए।
- v4+terms / traffic 5 dB: शाम को 5:00 बजे call कीजिए।
- v4+terms / voices 5 dB: शाम को 5 बजे pouch वाले में कितना खर्चा आएगा?

**14 (amit)** truth: price per piece कितना पड़ेगा?
- current / clean: price पर piece कितना पड़ेगा
- current / traffic 5 dB: Price per piece कितना पड़ेगा?
- current / voices 5 dB: प्राइस पर पीस कितना पड़ेगा
- v4 / clean: Price per piece कितना पड़ेगा?
- v4 / traffic 5 dB: Price per piece कितना पड़ेगा?
- v4 / voices 5 dB: price पर piece कितना पड़ेगा?
- v4+terms / clean: price per piece कितना पड़ेगा?
- v4+terms / traffic 5 dB: price per piece कितना पड़ेगा?
- v4+terms / voices 5 dB: price पर piece कितना पड़ेगा?

**15 (aarti_hi_customer)** truth: हम dry fruits और cashews pack करते हैं
- current / clean: हम dry fruits और cashews pack करते हैं।
- current / traffic 5 dB: हम dry fruits और cashews पैक करते हैं।
- current / voices 5 dB: हम dry fruits और cashews pack करते हैं।
- v4 / clean: हम dry fruits और cashews pack करते हैं।
- v4 / traffic 5 dB: हम dry fruits और cashews pack करते हैं।
- v4 / voices 5 dB: हम dry fruits और cashews pack करते हैं।
- v4+terms / clean: हम dry fruits और cashews pack करते हैं।
- v4+terms / traffic 5 dB: हम dry fruits और cashews pack करते हैं।
- v4+terms / voices 5 dB: हम dry fruits और cashews pack करते हैं।

**16 (rahul)** truth: आप लोग कहाँ से हो, Ahmedabad के पास?
- current / clean: आप लोग कहाँ से हो अहमदाबाद के पास
- current / traffic 5 dB: आप लोग कहां से हो? Ahmedabad से पास।
- current / voices 5 dB: हमें 10,000 pouch चाहिए 2 हफ्ते में
- v4 / clean: आप लोग कहाँ से हो? अहमदाबाद के पास।
- v4 / traffic 5 dB: आप लोग कहाँ से हो? अहमदाबाद के पास।
- v4 / voices 5 dB: हमें 10,000 pouch चाहिए Ahmedabad से 2 हफ़्ते में।
- v4+terms / clean: आप लोग कहाँ से हो? Ahmedabad के पास।
- v4+terms / traffic 5 dB: आप लोग कहाँ से हो? Ahmedabad के पास।
- v4+terms / voices 5 dB: हमें 10,000 pouch चाहिए, Ahmedabad से, 2 हफ्ते में।

**17 (priya)** truth: ठीक है, मैं सोचकर बताता हूँ
- current / clean: ठीक है मैं सोचकर बताता हूँ
- current / traffic 5 dB: ठीक है, मैं सोच कर बताता हूं।
- current / voices 5 dB: मैं सुठकर बताता हूँ
- v4 / clean: ठीक है। मैं सोचकर बताता हूँ।
- v4 / traffic 5 dB: ठीक है, मैं सोचकर बताता हूँ।
- v4 / voices 5 dB: ठीक है, मैं सोचकर बताता हूँ।
- v4+terms / clean: ठीक है। मैं सोचकर बताता हूँ।
- v4+terms / traffic 5 dB: ठीक है, मैं सोचकर बताता हूँ।
- v4+terms / voices 5 dB: ठीक है, मैं सोचकर बताता हूँ।

**18 (amit)** truth: 3 side seal pouch में कितना खर्चा आएगा?
- current / clean: 3 side seal pouch में कितना खर्चा आएगा
- current / traffic 5 dB: 3 side seal pouch में कितना खर्चा आएगा?
- current / voices 5 dB: मुझे मखाना के अंदर कितना खर्चा आएगा
- v4 / clean: 3 side seal pouch में कितना खर्चा आएगा?
- v4 / traffic 5 dB: 3 side seal pouch में कितना खर्चा आएगा?
- v4 / voices 5 dB: मुझे मखाना के 3 pouch में कितना खर्चा आएगा?
- v4+terms / clean: 3 side seal pouch में कितना खर्चा आएगा?
- v4+terms / traffic 5 dB: 3 side seal pouch में कितना खर्चा आएगा?
- v4+terms / voices 5 dB: मुझे मखाना के 3 side seal pouch में कितना खर्चा आएगा?

**19 (aarti_hi_customer)** truth: मेरी quantity लगभग पच्चीस हज़ार है
- current / clean: मेरी quantity लगभग 25,000 है।
- current / traffic 5 dB: मेरी quantity लगभग 25,000 है।
- current / voices 5 dB: मेरी quantity लगभग 25,000 है।
- v4 / clean: मेरी quantity लगभग 25,000 है।
- v4 / traffic 5 dB: मेरी quantity लगभग 25,000 है।
- v4 / voices 5 dB: मेरी quantity लगभग 25,000 है।
- v4+terms / clean: मेरी quantity लगभग 25,000 है।
- v4+terms / traffic 5 dB: मेरी quantity लगभग 25,000 है।
- v4+terms / voices 5 dB: मेरी quantity लगभग 25,000 है।
