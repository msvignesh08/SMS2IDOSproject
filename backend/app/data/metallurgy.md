# SMS-2 IDOS — PLANT KNOWLEDGE PACK

Domain reference injected into the local model's context so a small model
answers like a melt-shop engineer instead of like a general chatbot. A 7B model
does not reliably *know* this; given it in context, it can *use* it.

Keep it factual and specific to how THIS shop runs. If plant practice changes,
change it here — this file is the knowledge, not the code.

---

## 1. The route a heat takes

Two entry furnaces. A heat starts in exactly one and never both.

- **EAF** — Electric Arc Furnace. Heat numbers start **E**. Charge is hot metal
  plus scrap plus DRI, melted with electrical energy through graphite
  electrodes, with oxygen lancing for decarburisation and chemical heat.
- **ZPF** — Zero Power Furnace. Heat numbers start **F**. Runs on the chemical
  energy of the hot metal and oxygen, with no electrical input — hence "zero
  power". Higher hot-metal ratio, lower kWh/t, and it is the cheaper route
  when hot metal is available.

After melting and tapping:

1. **LRF-5 or LRF-6** — Ladle Refining Furnace. Arc reheating under a synthetic
   slag: final desulphurisation, alloy trimming to grade aim, temperature and
   inclusion control.
2. **Route decision** — either a **VD tank** (VD-2.1, VD-2.2, VD-3.1, VD-3.2)
   for vacuum degassing, or direct to a **caster** (BC-2, BC-3).
3. **Billet caster** — BC-2 or BC-3. Continuous casting into billets.
4. **Quality** — laboratory verification.

Ladle transfer cars move the ladle between units. They are logged against the
casting record, not the refining record.

**Plant day** runs 22:00 to 22:00 and is opened by C shift. Shifts are A, B, C.
A heat recorded at 23:41 belongs to the *next* calendar day's plant day. This
trips people up constantly — check it before calling a date wrong.

---

## 2. EAF and ZPF practice

### Charge

- **Hot metal (HM)** — liquid iron from the blast furnace, roughly 1350 °C,
  ~4.2% C, 0.6% Si, 0.1% P, 0.04% S. Carries chemical energy: its carbon and
  silicon burn and heat the bath. A higher HM ratio means less electricity.
- **Scrap** — HSC (heavy melting scrap), shredded, turnings. Cold charge:
  absorbs energy. Tramp elements (Cu, Sn, Ni, Cr) come in with scrap and
  **cannot be removed by refining** — they only accumulate. Grade limits on
  Cu/Sn are a scrap-selection problem, not a refining problem.
- **DRI / sponge iron** — direct reduced iron. Clean of tramps, high gangue.
  Fed continuously, it stabilises the bath and dilutes residuals.
- **HMC charge route** — the hot metal enters either via a **launder** (a
  refractory-lined trough) or by **top pouring**.

### Melting and refining

- **Power on / power off** bound the electrical period. **Tap-to-tap time
  (TTT)** is the cycle from one tap to the next — the single most important
  productivity number in the shop.
- **Oxygen lancing** decarburises (C + ½O₂ → CO) and oxidises Si, Mn, P, Fe.
  The CO boil stirs the bath and flushes hydrogen and nitrogen.
- **Slag** — lime (CaO) and dolomite form a basic slag. **Basicity
  B = CaO/SiO₂**, typically 2.5–3.5. Foaming slag (injected carbon reacting with
  FeO to make CO) shields the arc, saving refractory and energy.

### Dephosphorisation — the ZPF's whole purpose

Phosphorus removal needs conditions the opposite of carbon removal:

- **Low temperature** (P reverts as the bath heats)
- **High basicity** (B > 2.5)
- **High FeO in slag** (oxidising)

Reaction: `2[P] + 5(FeO) + 4(CaO) → (4CaO·P₂O₅) + 5[Fe]`

This is why de-P happens early and hot, and why a rising temperature with P
still high means trouble. If P is out of spec at tap, the causes are almost
always: slag too acidic, slag not oxidising enough, tapped too hot, or slag
carryover from the furnace reverting P back into the ladle.

### Desulphurisation — the LRF's job, not the furnace's

Sulphur removal needs a **reducing** environment — the opposite of de-P. So it
cannot be done well in an oxidising furnace, and is finished at the LRF under a
deoxidised slag with high basicity and strong stirring.

Reaction: `[S] + (CaO) + [Al] → (CaS) + Al₂O₃`

High S at the caster usually means: not enough time under the LRF slag,
insufficient argon stirring, an under-deoxidised slag (FeO too high), or slag
carryover from tapping.

---

## 3. Tapping and deoxidation

- **Tap temperature** must cover every downstream loss: transfer, LRF arrival,
  degassing, and the caster's superheat requirement. Too cold and the heat
  freezes or the caster runs slow; too hot and you burn refractory, degrade
  quality, and waste energy.
- **Deoxidation** — dissolved oxygen must be removed before casting or it forms
  blowholes and oxide inclusions. **Aluminium** (killed steel) or **silicon**
  (semi-killed).
- **Ferroalloys** — FeMn, FeSi, SiMn, FeCr added at tapping or the LRF to hit
  grade aim. Recovery is never 100%: expect roughly 85–95% for Mn, 80–90% for
  Si, lower if the bath is still oxidising. Alloy added at *tapping* is not
  charge material and must not be counted in the charge mix — it inflates the
  metallic charge and silently corrupts HM%.

---

## 4. Vacuum degassing (VD / VOD)

Under vacuum (typically < 1 mbar), dissolved gases are driven out:

- **Hydrogen** — the critical one. Above ~2 ppm causes flaking and hairline
  cracks in heavy sections. Degassing brings it under control; nothing else
  does.
- **Nitrogen** — removed more slowly. Mostly controlled by limiting air ingress.
- **Inclusion flotation** — argon stirring under vacuum floats alumina
  inclusions into the slag, cleaning the steel.

Degassing loses temperature — typically 30–50 °C — which the tap temperature
must anticipate.

---

## 5. Continuous casting

- **Superheat** = tundish temperature − liquidus. Typically 20–40 °C. Too high
  gives centreline segregation and breakout risk; too low causes nozzle
  clogging and freezing.
- **Casting speed** must match superheat and section size. Faster casting means
  a thinner solidified shell at mould exit and a higher breakout risk.
- **Common defects and their usual causes:**
  - *Centreline segregation* — superheat too high, or too fast
  - *Corner cracks* — mould taper, non-uniform cooling
  - *Blowholes / pinholes* — insufficient deoxidation, moisture
  - *Inclusions* — poor LRF flotation, slag carryover, reoxidation from air
  - *Breakout* — shell too thin: excessive speed, low superheat, mould level
    instability

---

## 6. Delays

Delays are logged against an **agency** (the department responsible) and then a
specific **reason** under it. Agencies include Mechanical, Electrical,
Refractory, Operational, External, and Planned Shutdown.

Reading a delay pareto: the agency with the most *minutes* is not necessarily
the most fixable. A single long planned shutdown outweighs many short
mechanical stops, but the short repeated ones are usually where the recoverable
time is. Always report both minutes and occurrence count.

---

## 7. Numbers worth knowing for this shop

| Quantity | Typical range | Note |
|---|---|---|
| Hot metal temperature | 1300–1400 °C | at charging |
| Tap temperature | 1620–1680 °C | grade and route dependent |
| Tap-to-tap time | 55–90 min | the productivity number |
| EAF energy | 300–450 kWh/t | falls sharply as HM ratio rises |
| Oxygen | 25–45 Nm³/t | |
| Slag basicity | 2.5–3.5 | CaO/SiO₂ |
| Hydrogen after VD | < 2 ppm | above this risks flaking |
| Caster superheat | 20–40 °C | above liquidus |
| Metallic yield | 88–96% | tapped to billet |

Treat these as orientation, not as this plant's spec. **When a question turns
on a real number, read it from the plant's own records with the tools — do not
answer from this table.**

---

## 8. How to behave

- Read the plant's data before judging a heat. A specific heat's chemistry,
  timings and delays are one tool call away.
- Grade aims are set by this plant's administrator. Call the grade tool; never
  recite a spec from memory.
- Say when a field is blank. Blank is not zero, and it is not an operator
  error — heats imported from the old workbook were never subject to this
  app's entry rules.
- Units always: °C, t, kg, kWh, Nm³, minutes, ppm, %.
- If you do not know, say so and say what would answer it.
