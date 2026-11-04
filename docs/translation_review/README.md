# Reviewed translations

This folder lists, for every language, each translation that the "reviewed translations" change brought into
`locale/<lang>/LC_MESSAGES/strings.po`, so that native speakers can check and approve them.

## Where they come from

The translations of all languages were brought up to date with the code of 19 September 2026 in a fork of FlatCAM
Evo, then moved onto the current catalogs with `scripts/i18n_tool.py` (`extract` + `merge`, then each reviewed
translation applied to the same English text):

- **Fuzzy entries**, which the program never shows (users saw English), were confirmed or corrected.
- **Missing translations** were translated.
- **Format errors** were fixed (a broken `%s`, `{name}`, `%%` or HTML tag can make the program fail).
- **Wrong or poor translations** were corrected: wrong meaning, mistranslated CNC/PCB term, English left in the text,
  grammar, a missing part of the sentence.

Method: for each chunk of entries a translator model made the changes and gave a reason for each; a second,
independent reviewer model accepted, rejected or corrected every change; every change was checked with
`scripts/i18n_tool.py` (placeholders, `%%`, HTML tags, `<argument>` names, newlines, and a real `%` formatting test).
Turkish was also checked on screen. A translation that Marius's catalog had changed since September was kept as it
is; nothing that failed the check was applied.

**These are machine-assisted changes. They need a native translator's approval.**

| Language | Reviewed entries in this change | Still untranslated (newer texts) |
|---|---|---|
| Deutsch (de) | 1486 | 443 |
| Español (es) | 1042 | 443 |
| Français (fr) | 1305 | 443 |
| Italiano (it) | 894 | 443 |
| Português do Brasil (pt_BR) | 574 | 443 |
| Română (ro) | 924 | 443 |
| Русский (ru) | 1353 | 443 |
| Türkçe (tr) | 1097 | 443 |
| 简体中文 (zh) | 1009 | 443 |

The texts added to the program after September are not translated here; they show in English until they are.

## The texts added after September (2026-10-21)

The 635 texts that were still untranslated in each language (and the fuzzy "Calibration" entry of de, fr, pt_BR and
ru) were translated on 2026-10-21 in the same way: a translator model with the glossary and the existing translations
of the same words, an independent reviewer model that corrected the draft, and the `scripts/i18n_tool.py` checks
(placeholders, `%%`, HTML tags, `<argument>` names, line breaks, a real `%` formatting test). These entries are not in
the `<lang>.md` / `<lang>.csv` lists; in the `.po` file each one carries the comment
`# auto-review 2026-10-21: new` (or `fuzzy corrected` / `fuzzy confirmed`). They also need a native translator's
approval.

## Arabic, a new language (2026-11-04)

Arabic (`ar`, العربية) was added on 2026-11-04 as a new language, and **the whole Arabic catalogue is machine-assisted**:
all 5842 texts were translated into Modern Standard Arabic by a translator model, chunk by chunk with one glossary
([ar_glossary.csv](ar_glossary.csv): the Arabic term chosen for every key term, with a note), then an independent
reviewer model checked every entry (meaning, PCB/CNC terms, placeholders, `%%`, HTML tags, line breaks, `<argument>`
names) and corrected 425 of them; `scripts/i18n_tool.py check ar` reports 0 placeholder errors and 0 warnings. Every
entry carries the comment `# auto-review 2026-11-04: new` and **needs a native speaker's approval**. `ar.md` / `ar.csv`
list every entry (with the reviewer's correction as the reason where there was one). When Arabic is selected the
interface is mirrored right to left; the canvas, the coordinates, the code editors and the Tcl shell stay left to right.

Kept in Latin letters on purpose: Gerber, Excellon, G-code, GRBL, Marlin, FluidNC, Tcl and the Tcl command names and
options, file extensions, units (`mm`, `in`), `<argument>` names, key names (Ctrl, Shift, Alt, F3), default file and
object names. Numbers are written in Western digits (0-9), as the program writes them.

## How to approve

- `<lang>.md`: readable table (type, English, old translation, new translation, reason).
- `<lang>.csv`: the same rows for a spreadsheet (UTF-8). Fill in *Approved (yes/no)* and, if needed, your own text
  in *Translator note*, and send the file back (an issue with the *Translation problem* form, or a pull request).

You can also work directly in the `.po` file (for example with Poedit): every entry changed this way carries the
comment `# auto-review 2026-10-09: <type>` (Arabic: `# auto-review 2026-11-04: new`). Delete the comment when you have checked the entry, then run

    python scripts/i18n_tool.py check <lang>
    python scripts/i18n_tool.py compile <lang>

## Change types

| Type | Meaning |
|---|---|
| format | Format fix (placeholder / %% / tag / newline error that could break the program) |
| corrected | Correction of an existing translation |
| fuzzy corrected | A fuzzy (unused) translation was corrected; the program now uses it |
| fuzzy confirmed | A fuzzy (unused) translation was right; the program now uses it |
| untranslated copy | The translation was a copy of the English text; now translated |
| new | New translation (the text had no translation) |
| manual | Terminology fix made by hand after checking the program in that language |
