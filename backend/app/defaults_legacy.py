"""Exact copies of earlier shipped texts (settings version 4). A saved value still equal to one of these was
never customised, so the one-time settings upgrade replaces it with the current default."""

V4_TEXTS: dict[str, str] = {'call_purpose': 'Outbound call to a business lead. Find out if they need custom printed flexible packaging '
                 '(pouches), qualify them, and set up the next step.\n'
                 'Call flow:\n'
                 '1) The opening line already asked if they have two minutes.\n'
                 '2) If yes: in ONE short sentence say why you called (we make custom printed packaging '
                 'pouches for brands, even in small quantities), then ask the first qualification question.\n'
                 '3) If busy / call later: ask for a convenient time, confirm it, end politely.\n'
                 '4) If not interested or wrong person: thank them and end politely.\n'
                 '5) Ask the qualification questions one at a time, then offer the next step: a call back '
                 'from our packaging expert with an exact quote, details on WhatsApp, or a Free Sample Kit.',
 'inbound_purpose': "Inbound call: the customer called {company_name}'s number and you answered.\n"
                    'Call flow:\n'
                    '0) If they say they got or missed a call from this number: yes, we had called them. '
                    'Give the reason in one short sentence, ask if now is a good time, then continue like an '
                    'enquiry.\n'
                    '1) Find out why they called and help with that first — answer questions about our '
                    'pouches, printing, samples or ordering in a few words.\n'
                    "2) If you don't know their name yet, ask it politely early on, and their business / "
                    'product.\n'
                    '3) While helping, qualify them naturally with the qualification questions (skip what is '
                    'already known).\n'
                    '4) Offer the next step: a call back from our packaging expert with an exact quote, '
                    'details on WhatsApp, or a Free Sample Kit. Confirm a convenient time for the call '
                    'back.\n'
                    '5) An existing customer asking about an order, sample, delivery or payment, or making a '
                    'complaint: apologise if needed, take the details, never guess a status, promise a call '
                    'back from our team with a time.\n'
                    '6) They want a specific person or a human: take a short message and a convenient '
                    'call-back time.\n'
                    '7) A vendor, a job seeker or a wrong number: answer briefly and close politely.\n'
                    '8) If they only wanted to check something: answer, ask once if there is anything else, '
                    'thank them, end.',
 'knowledge_base': 'PouchWale is the modern packaging arm of Gujarat Print Pack Publications (GP3), Mehsana, '
                   'Gujarat. We make short-run, custom printed flexible packaging for emerging D2C brands, '
                   'with low MOQs and fast turnaround.\n'
                   'Pouch types: Stand-up pouch with zipper (rice, grains, pet food, snacks, dry fruits); '
                   'Center seal / pillow pouch (namkeen, spices, detergents); 3-side seal pouch (sachets, '
                   'cosmetics); Spout pouch (sauces, juices, edible oil, baby food); Special shape pouch '
                   '(confectionery, kids snacks); Vacuum pouch (meat, seafood, ready meals); Quad seal pouch '
                   '(premium coffee, large pet food, protein powder); Freeze-grade pouch (frozen food, ice '
                   'cream).\n'
                   'Certifications: GMP, ISO 9001, ISO 14001, ISO 45001, EcoVadis Silver 2024.\n'
                   'Offer: Free Sample Kit. Contact: info@pouchwale.com.\n'
                   'MOQ: [FILL IN]. Pricing: [FILL IN]. Lead time: [FILL IN]. Until these are filled in, '
                   'never quote numbers — say our packaging expert will share an exact quote.',
 'objection_handling': 'Use Acknowledge -> Clarify -> Respond -> Advance. Never argue, never disparage '
                       'competitors.\n'
                       '- Price too high: acknowledge, ask what they compare against, explain value (print '
                       'quality, low MOQ, fast turnaround), offer an exact quote from our expert.\n'
                       '- MOQ too high / small quantity: ask their monthly quantity, mention we specialise '
                       'in short runs for growing brands, offer the expert call for exact MOQ.\n'
                       '- Already have a supplier: respect it, ask what they would improve, offer a Free '
                       'Sample Kit to compare.\n'
                       '- Need it urgently / lead time: ask the date they need it by, offer an expert call '
                       'to confirm timelines.\n'
                       '- Send details / catalogue: agree, confirm WhatsApp on this number, ask one '
                       'qualifying question first.\n'
                       '- Call later / busy: ask a convenient time, confirm it, end politely.\n'
                       'If they push back twice on the same thing, or ask for final price / discount / firm '
                       'dates, hand off: offer a call back from our packaging expert.'}


# Version 6-7 texts (before "our team sends the details" and the exact ₹199 wording, 2026-11-19).
V6_TEXTS: dict[str, str] = {
    'knowledge_base': 'Company: PouchWale (pouchwale.com), Gujarat, India. Contact: info@pouchwale.com.\nWhat we do: custom printed pouches for brands. Say it like: "हम custom pouch बनाते हैं — जैसे namkeen, dry fruits, spices के packets होते हैं, वैसे आपके brand के लिए custom printed."\nWhy us: low MOQ and fast turnaround.\nMOQ: starts from 1,000 pieces ("MOQ एक हज़ार pieces से start होता है").\nPrice / rate: always quoted PER PIECE (pcs), NEVER per kg. There are no fixed rates to tell: our team shares the exact per-piece quote once they know the size, quantity and print.\nSize / dimensions: the customer gives us the pouch size they need, and our team also helps them choose the right size.\nFree sample kit: the kit is free, the customer pays only ₹199 shipping ("Sample kit free है, बस one ninety nine rupees shipping charges लगते हैं"). Suggest it to customers who are undecided or comparing.\nCertifications: GMP, ISO 9001, ISO 14001, ISO 45001, EcoVadis Silver 2024.\n\nWhich pouch for which product (suggest these; for a product not listed, the team advises):\n- Milk powder, beverage mixes, hot chocolate: 3-side seal pouch or center seal pouch\n- Coffee, tea: quad seal bag\n- Rice (premium / basmati): quad seal bag or center seal pouch\n- Nuts, dry fruits, cashews: standup pouch\n- Namkeen, snacks, popcorn, bakery: standup pouch, 3-side seal pouch or center seal pouch\n- Chocolates, candy: 3-side seal pouch or standup pouch\n- Grains, flour: center seal pouch\n- Spices, seasonings, masalas: standup pouch, sachets or center seal pouch\n- Paneer, tofu, dairy products: vacuum / high-barrier pouch\n- Frozen or fried foods: freeze-grade pouch\n- Beverages, juices: spout pouch\n- Sauces, ketchup: spout pouch\n- Nutritional supplements: standup pouch or center seal pouch\n- Cosmetics, beauty: special shape pouch or standup pouch; 3-side seal pouch for items like face masks\n- Pet food (dog food, cat food): standup pouch or center seal pouch\n- Seeds, fertilizers, agro chemicals: center seal pouch, standup pouch or quad seal bag\n- Khakhra, papad: standup pouch or 3-side seal pouch\n- Medicines, pharmaceuticals, ayurvedic supplements: center seal pouch or standup pouch\n- Mukhwas, supari, mouth freshener: standup pouch (retail) or sachet (small packs)\n- Ready-to-eat / instant meals: standup pouch or 3-side seal pouch\n- Oats, granola, breakfast cereals: standup pouch',
    'objection_handling': 'Use Acknowledge -> Clarify -> Respond -> Advance. Never argue, never disparage competitors.\n- Price too high / what is the rate: we quote per piece (never per kg); acknowledge, explain value (print quality, low MOQ, fast turnaround), offer an exact per-piece quote from our team.\n- Asks the price per kg: say politely that we quote per piece, not per kg, and the team will share it.\n- MOQ too high / small quantity: our MOQ starts from just 1,000 pieces; ask their quantity per order or per month.\n- Already have a supplier: respect it, ask what they would improve, suggest the free sample kit (only ₹199 shipping) to compare.\n- Undecided / wants to see the quality first: suggest the free sample kit (only ₹199 shipping).\n- Need it urgently / lead time: we have fast turnaround; ask the date they need it by; the exact delivery date the team confirms.\n- Send details / catalogue: agree, confirm WhatsApp on this number, ask one qualifying question first.\n- Call later / busy: ask a convenient time, confirm it, end politely.\nIf they push back twice on the same thing, or ask for a final price, a discount or firm dates, hand off: offer a call back from our team.',
}
