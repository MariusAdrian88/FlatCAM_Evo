# Translating FlatCAM Evo

Thank you for helping. You do not need to be a programmer: a translation is a text file with the English text and
your translation next to it. This page explains where the files are, which mistakes can break the program and how
to send your work.

## Where things stand

    python scripts/i18n_tool.py stats

prints, for every language, how many texts are translated, untranslated or fuzzy, how many have a placeholder error
or a warning (see *Checking your work*) and whether the compiled `.mo` file is up to date.

If the program shows a text in English although your language translates everything, the `.po` is older than the
code: the maintainers update it with `extract` and `merge` (see the last section), then the new texts can be
translated.

**Waiting for approval:** many translations were corrected or added with machine help (one model translated, a
second one reviewed every change, and the check below passed for each). They need a native speaker's approval.
[`docs/translation_review/`](translation_review/README.md) lists every such entry of each language, with the old
and the new text and the reason, as a readable table and as a spreadsheet with an *Approved* column. In the `.po`
file these entries carry the comment `# auto-review 2026-10-09: <type>`; delete it when you have checked the entry.

## Ways to help (easiest first)

1. **Report a bad text you saw in the program.** Open an issue with the *Translation problem* form: the language,
   the text, what it should be, and a screenshot if you can.
2. **Edit the translation file itself.** `locale/<lang>/LC_MESSAGES/strings.po`, with [Poedit](https://poedit.net)
   (free) or any text editor. Send it as a pull request, or attach it to an issue.
3. **Add a new language** (see below).

## The files

| File | What it is | Do you edit it? |
|---|---|---|
| `locale/<lang>/LC_MESSAGES/strings.po` | the translation: every English text (`msgid`) and its translation (`msgstr`) | yes |
| `locale/<lang>/LC_MESSAGES/strings.mo` | the compiled file the program reads | no: made from the `.po` |
| `locale_template/strings.pot` | all English texts of the program (the template) | no: made from the code |
| `locale_template/glossary_terms.txt` | the key CNC/PCB terms with a short explanation | only to add a term |

The language codes are the folder names: `ar`, `de`, `es`, `fr`, `it`, `pt_BR`, `ro`, `ru`, `tr`, `zh`.

## Rules: what must stay as it is

Some parts of a text are filled in or interpreted by the program. If they are changed, the program shows a wrong
message or **fails with an error**. The check tool (below) finds these mistakes.

- **Placeholders** stay exactly as they are, the same number of each: `%s`, `%d`, `%.4f`, `%(name)s`, `{name}`.
  Their order may change only for `%(name)s` and `{name}`; keep `%s`/`%d` in the English order. Do not add a
  placeholder to a text that has none: the program shows it as it is (`%s`).
- **A percent sign** in a text that has placeholders is written `%%` (for example `100%%`). A single `%` there makes
  the program fail.
- **HTML tags** stay: `<b>`, `</b>`, `<br>`, `<span ...>`, `<font ...>`. Translate the text between them.
- **Line breaks at the start or the end** of a text stay. Line breaks inside a text may move.
- **Argument names in angle brackets** (`<command_name>`, `<value>` in the Tcl command help) are best kept in English,
  like the command names and options next to them.
- **Names of formats, programs and commands** are usually kept: Gerber, Excellon, G-code, GRBL, Marlin, Tcl, the Tcl
  command names (`isolate`, `drillcncjob`...).
- **Units and numbers** are written as the program writes them (`mm`, `in`, `0.1`).

Use the key terms consistently: `python scripts/i18n_tool.py glossary <lang>` shows how your language translates
them today (`--out glossary.csv` writes the table to a file for a spreadsheet), and
`locale_template/glossary_terms.txt` explains what each one means. A common trap: *Tool* and *Tools Table* mean the
**cutting** tool (end mill, drill bit, laser), not a software tool.

## Checking your work

You only need Python 3 (no packages). In the folder of the program:

    python scripts/i18n_tool.py check <lang>      # placeholder errors, warnings, untranslated and fuzzy texts
    python scripts/i18n_tool.py compile <lang>    # makes strings.mo from strings.po
    python scripts/i18n_tool.py stats             # the status table of all languages

`check` must report **0 placeholder errors**: an error is a translation that makes the program fail when it fills in
the values (a lost or extra `%s`, a single `%`, a changed `{name}`). The **warnings** do not make it fail, but most of
them are mistakes the user sees: a lost or extra HTML tag, a placeholder written differently (`% s`), a `%s` added to
a text that has none, a translated argument name, a missing line break at the start or the end. Look at each one.

Texts marked *fuzzy* (Poedit: "needs work") are **not used** by the program: users see English instead. Poedit shows
the previous English text of a fuzzy entry; check the translation against the new one and remove the mark.

## Seeing your translation in the program

**Preferences → General → App Preferences → Languages**, choose the language, *Apply Language*; the program
restarts.

Remember to run `compile` after editing the `.po`: the program reads the `.mo`.

## Adding a new language

1. `python scripts/i18n_tool.py init <code>` makes `locale/<code>/LC_MESSAGES/strings.po` with every text
   untranslated (`<code>`: `pl`, `pt_PT`, `ja`...).
2. Translate (all of it, or start with the menus and the tools you use).
3. `python scripts/i18n_tool.py check <code>` and `compile <code>`.
4. Add the language and its name **in that language** to `languages_dict` in `appTranslation.py`, or ask the
   maintainers to do it: sending the `.po` is enough.

## When the program's texts change (maintainers)

    python scripts/i18n_tool.py extract           # locale_template/strings.pot from the code
    python scripts/i18n_tool.py merge ar de es fr it pt_BR ro ru tr zh
    python scripts/i18n_tool.py compile
    python scripts/i18n_tool.py stats             # --markdown <file> also writes the table to a file

`extract` also lists the `_()` calls whose argument is not a string literal: it only sees the texts written inside
`_()`. Some are expected: they translate a text held in a variable (`_(self.label_text)` in
`appGUI/preferences/OptionUI.py`, `_(label)` in `appDatabase.py`). A formatted text such as `_("Line %s" % n)` is a
text that no language can translate: write `_("Line %s") % n`.
`merge` keeps every translation whose English text is unchanged, adds the new texts untranslated and keeps texts no
longer in the code as obsolete entries (`#~`), so a translation comes back if its text does.

Translations are contributed under the license of the project (MIT, see `LICENSE`).
