# Hearing the customer: speech-recognition benchmark (2026-11-25)

## Why

On test calls the agent misheard customers:
- a short "हाँ जी" became "Funding" and "Tan" (call 78);
- "MOQ" became "MOV", "standup pouch" became "stand up outfit", and "एक लाख" became "₹1,00,000" (call 82).

The team also asked how she copes when the customer is in traffic or other people are talking nearby.

## How it was measured (repeatable)

| What | Tool | Set |
|---|---|---|
| Accuracy, clean and in noise | `backend/tools/stt_benchmark.py` | 20 customer-style sentences (MOQ, pouch types, numbers, short replies, a company name) spoken by 4 Sarvam voices (2 male, 2 female), sent through the 8 kHz mu-law phone codec. Each sentence was tested clean, in traffic noise at 10 and 5 dB SNR, and with two other people talking at 10 and 5 dB SNR. That is 100 clips per setup. |
| Accuracy on real calls | scratch script (private audio, not in the repo) | The customer side of calls 78 and 82: 55 utterances, 121 s. Scored on the 7 lines whose real words the team confirmed (9 key words). |
| Speed, and words invented from noise | `backend/tools/stt_latency_noise.py` | 10 clips streamed in real time (end of speech → final transcript), and 6 clips with no speech: 3 loud traffic, 3 faint line hiss. |
| Voice check | `app/services/audio.py: voiced_share` | Every set above, offline. |

Every clip is streamed to Sarvam's realtime STT exactly as on a call (same URL parameters, 60 ms frames).

**Scores** (truth and transcript normalised the same way):
- **Word error rate (WER):** word-level edit distance ÷ number of words in the truth. Lower is better.
- **Key terms:** the share of the words that matter that were heard right. Each term has a few accepted spellings.
- **SNR:** the loudness of the voice over the loudness of the noise. At 5 dB the noise is about half as loud as the voice.

Raw output is in this folder: `results.json`, `scores.json`, `report.md` (what each setup heard per sentence), `latency_noise.json`, and `clean/` (the synthesised sentences).

## Results

### 1. Labelled set: 100 clips per setup

| Setup | Clean | Traffic 10 dB | Traffic 5 dB | Other voices 10 dB | Other voices 5 dB | **WER, all** | **Key terms, all** |
|---|---|---|---|---|---|---|---|
| current: `saaras:v3-realtime` | 4.5 % | 6.0 % | 6.0 % | 14.3 % | 33.1 % | **12.8 %** | **91.2 %** |
| `saaras:v4` | 3.0 % | 3.0 % | 3.0 % | 9.0 % | 29.3 % | **9.5 %** | **95.1 %** |
| `saaras:v4` + our key terms | 0.8 % | 1.5 % | 1.5 % | 13.5 % | 30.1 % | **9.5 %** | **95.6 %** |

- **Traffic hardly matters to any setup.** Other people talking is the hard case for all three.
- **Key terms under other voices at 5 dB:** 73.2 % (current), 82.9 % (v4), 90.2 % (v4 + key terms).
- **Precision:** with 20 clips per condition, differences of a few points within one condition can be chance. The overall figures (100 clips) are firmer.

### 2. Real calls 78 and 82: key words in the confirmed lines

| Setup | Key words right |
|---|---|
| current | 4 of 9 |
| v4 | 6 of 9 |
| v4 + key terms | **7 of 9** |

- **Fixed by v4 + key terms:**
  - The second short "हाँ" of call 78 (the current model heard it as "नहीं", the opposite).
  - "MOQ".
  - "standup pouch", twice.
- **Wrong with every setup:** the company name.
- **New mistakes v4 made on real speech:**
  - "भिजवा दीजिए" heard as "दिमाग दीजिए".
  - One "नहीं" dropped from "नहीं चाहिए Madam नहीं चाहिए".
- **Short sounds:** v4 also writes customers' "हम्म" sounds as "हाँ" / "जी". The agent treats all of these as reactions.

### 3. Speed, and words invented from noise

| Setup | Final transcript after the end of speech (median / max) | Clips with no speech in which a word was invented |
|---|---|---|
| current | 0.79 s / 1.08 s | 3 of 6: "4", "4", "वो", all from loud traffic |
| v4 + key terms | 0.74 s / 0.92 s | 3 of 6: "हाँ" ×3, all from loud traffic |

Faint line hiss produced no words from either model.

### 4. The voice check (new in the app)

A transcript is only answered if the audio behind it sounds like a human voice. The test: share of active 40 ms frames that repeat at a voice pitch (80-400 Hz), measured in the 100-1000 Hz band. A share of 0.2 or less means "no voice".

| Audio | Voice-like share | Blocked |
|---|---|---|
| loud traffic, nobody speaking | 0.07 | 6 of 6 |
| deep rumble, nobody speaking | 0.04-0.11 | 6 of 6 |
| benchmark speech, clean | 0.86 or more | 0 of 20 |
| benchmark speech in traffic, 10 / 5 dB | 0.49 / 0.43 or more | 0 of 40 |
| benchmark speech among other voices, 10 / 5 dB | 0.77 / 0.68 or more | 0 of 40 |
| real customer utterances (calls 78, 82; 52 long enough to judge) | median 0.97 | 1: a voiceless burst no recogniser heard words in |

- **Speed:** the check takes about 18 ms for 5 s of audio.
- **Off switch:** it uses the same switch as the quiet-voice filter (`BACKGROUND_VOICE_FILTER`).

A first version without the 100-1000 Hz band let deep rumble look like a voice. A second version that boosted high frequencies hid the voice in traffic hiss. Both were measured and dropped.

## Value added

| Change | Before → after | Status |
|---|---|---|
| Voice check | loud traffic with nobody speaking turned into an answered word in 3 of 3 clips (either model) → 0 of 3; real speech blocked: 0 | in the app |
| Speech model `saaras:v4` + key terms | WER 12.8 % → 9.5 % (−3.3 points, −26 % relative) | waiting for the team's go-ahead |
| | key terms 91.2 % → 95.6 % | |
| | key terms with other voices at 5 dB: 73.2 % → 90.2 % | |
| | real calls, confirmed key words: 4/9 → 7/9 | |
| | final transcript 0.79 s → 0.74 s (median) | |

## Limits

- Synthetic voices speak more cleanly than real callers. The synthetic traffic is an approximation: its rumble, engine, hiss and one horn are seeded and made up.
- Truth for real calls covers only 9 words in 7 lines.
- Overlapping speech stays the weak spot. With another person as loud as the customer, about 30 % of words are wrong with every setup, and no setting fixes that on a single phone line.

## Cost of this measurement

- **Voice:** 665 characters (synthesised once and kept).
- **Speech recognition:** about 32 minutes of audio in total, across 3 setups, the real calls, the speed test and the noise test.
