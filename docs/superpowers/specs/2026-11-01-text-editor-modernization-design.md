# Text Editor Modernization — Design

Date: 2026-11-01. Status: approved approach A (user, 2026-11-01).

## Goal

Make `AppTextEditor` a modern text editor in its 2 modes:

- **Code mode** (`plain_text=True`): Script, CNCJob review, Source view, G-code editor, Levelling, SolderPaste,
  `App.init_code_editor`. Plain text, line gutter, highlighting.
- **Document mode** (`plain_text` falsy): `DocumentObject`. Rich text (`QTextEdit`).

Keep every current feature. Engine stays `QPlainTextEdit` / `QTextEdit` (no QScintilla, no new dependency).

## Baseline (2026-11-01, 200k G-code lines, scratchpad `bench_editor_load.py`)

| Step | Now | Target |
|---|---|---|
| `load_text()` | 1.06 s | ≤ 1.0 s |
| scroll to end | 0.02 s | ≤ 0.05 s |
| highlight of all matches, 200k matches (`G01`) | 9.55 s, GUI frozen | ≤ 0.2 s |
| typing / scrolling | smooth | smooth with folding + minimap on |

## Current defects to fix

1. Find-all builds one `ExtraSelection` per match over the whole document (`appTextEditor.py:525`): 9.5 s freeze.
2. Find is always case-sensitive; no regex, no whole word, no match count, no Find Previous. End of document opens
   a modal `FCMessageBox`.
3. Gutter colors hard-coded (`lightGray`, `blue`); dark theme inverts each hex digit of a color.
4. One regex rule set for G-code, Gerber, Excellon, Tcl and R keywords (`MyHighlighter`). Some rules are broken
   (`r"\\b"` in a raw string matches a literal backslash: D-codes, reserved words, constants never match).
5. Document mode: B/I/U and alignment do not follow the cursor; `font_size_cb.setCurrentIndex()` runs before
   `addItems()` (`DocumentObject.py:113`); Save writes `toPlainText()`, so a `.FlatDoc` loses its formatting.
6. Each caller rewires `buttonOpen` / `buttonSave` by hand (`clicked.disconnect()` then `connect`).

## Architecture

New package `appEditors/text_editor/`. `appEditors/appTextEditor.py` keeps `AppTextEditor` as the facade.

| Module | Part | Depends on |
|---|---|---|
| `themes.py` | `EditorPalette` (light, dark) for gutter, current line, bracket match, find match, fold marker, minimap, and the token colors. `palette_for(theme)` | Qt only |
| `lexers.py` | `Lexer` base (`rules`, `comment_prefix`, `fold_regions(text)`), `GCodeLexer`, `GerberLexer`, `ExcellonLexer`, `TclLexer`, `PlainLexer`, `detect_language(text)` | `themes` |
| `highlighter.py` | `LazyHighlighter(QSyntaxHighlighter)`: the current lazy logic (only shown blocks, state -1 = never highlighted) with a `Lexer` | `lexers` |
| `code_edit.py` | `CodeEdit(FCPlainTextAreaExtended)`: gutter (line numbers + fold markers), current line, bracket match, editing aids, zoom, whitespace, wrap, fold/unfold, `goto_line(n)`, fast `load(text)` | `highlighter`, `themes`, `folding` |
| `folding.py` | `FoldModel`: fold regions from `Lexer.fold_regions()` on a debounced full-text scan; hides blocks with `QTextBlock.setVisible(False)` | `lexers` |
| `find_bar.py` | `FindBar(QWidget)`: find + replace fields, case / whole word / regex toggles, "N of M", Prev/Next, Replace, Replace All, close. Search runs on `toPlainText()` with Python `re` | `code_edit` (duck-typed editor) |
| `minimap.py` | `Minimap(QWidget)`: cached density image of line lengths + viewport rectangle; click/drag scrolls | `code_edit` |
| `status_bar.py` | `EditorStatusBar`: Ln/Col, selection length, line count, language, EOL, modified, zoom | `code_edit` |
| `rich_toolbar.py` | `RichTextToolbar(QToolBar)` for Document mode: font, size, B/I/U/S, text color, highlight color, alignment (exclusive), bullet / numbered list, heading level, clear format. Follows `currentCharFormatChanged` and `cursorPositionChanged` | Qt only |

`FCTextAreaLineNumber` in `GUIElements.py` is removed after phase 1 (only `AppTextEditor` and
`tests/test_lazy_highlight.py` use it). `FCPlainTextAreaExtended` stays: `CodeEdit` inherits its completer, context
menu, `add_action_to_context_menu`, Ctrl+Shift+V path paste and `comment()`.

### Facade contract (kept from phase 1)

`AppTextEditor(app, text=None, plain_text=None, color_dict=None, parent=None, language=None)` keeps:
`code_editor`, `t_frame`, `work_editor_layout`, `buttonOpen`, `buttonSave`, `buttonRun`, `buttonPrint`,
`buttonPreview`, `button_update_code`, `entryFind`, `entryReplace`, `sel_all_cb`, `buttonFind`, `buttonReplace`,
`load_text()`, `handleOpen()`, `handleSaveGCode()`, `handleFindGCode()`, `handleReplaceGCode()`,
`handleTextChanged()`, `handlePrint()`, `handlePreview()`, `set_callback()`, `script_owner`.
`entryFind`, `entryReplace`, `sel_all_cb`, `buttonFind`, `buttonReplace` become attributes of the `FindBar`
(aliases on the facade), so the tests and callers keep working until phase 5.

## Layout

Right side (editor tab), top to bottom:

1. Top toolbar row: Open, Save (red when modified), Print Preview, Print, Run (Script only), Find (Ctrl+F),
   Wrap, Whitespace, Minimap toggle. Document mode adds the `RichTextToolbar` row below it.
2. Find bar (hidden until Ctrl+F / Ctrl+H; Esc closes): `[find] [Aa] [W] [.*] N of M [↑] [↓]` and
   `[replace] [Replace] [All]`.
3. Body: gutter | text | minimap.
4. Status bar.

Left side (Properties panel): code-mode objects keep their panels. Document mode keeps only the document settings:
default font + size (applied to the whole document when changed), tab size, selection color, autocompleter.
The B/I/U, font color and alignment controls move to the `RichTextToolbar`.

## Behavior

### Find / Replace
- Debounce 150 ms. Count with `re.finditer` on the text (cached until `contentsChange`). Highlight only the matches
  inside the shown blocks (recomputed on `updateRequest`), so cost does not grow with the match count.
- Next/Prev wrap around without a dialog; the status shows "Wrapped". Enter = next, Shift+Enter = previous, F3 /
  Shift+F3 too.
- An invalid regex shows a red field and a tooltip, no exception.
- Replace All: one edit block = one Undo step, from the document start (keeps the current tested behavior).
  With regex on, `\1` groups work in the replace text.
- Ctrl+F with a selection on 1 line puts it in the find field.

### Go to Line
`CodeEdit.goto_line(n)` counts blocks, not wrapped lines (current tested behavior). Ctrl+G in `MainGUI.goto_text_line`
calls it; a wrong value warns and keeps the cursor.

### Lexers
- G-code: comments `( … )` and `; …`, G/M/T words, axis words X/Y/Z/I/J/K/A/B/C with numbers, F/S, N line numbers,
  `%`. Fold regions: header up to the first tool change; each `T<n>` / `M6` block to the next one.
- Gerber: `%…%` extended commands, D-codes, G-codes, X/Y/I/J coordinates, `G04` comments, attributes `%TF/%TA/%TO`.
  Fold: aperture macro `%AM…%` bodies, `G36…G37` regions.
- Excellon: header `M48`…`%`, `T<n>C<d>` definitions, X/Y, `;` comments. Fold: header, each tool block.
- Tcl/FlatScript: `#` comments, strings, `$vars`, `-options`, the app Tcl command names (`app.regFK.myKeywords`),
  braces. Fold: `{ … }` bodies that span lines.
- `detect_language()` from the first 200 lines when the caller passes no `language`.
- Callers pass `language`: Script → `tcl`, CNCJob / G-code editor / Levelling / SolderPaste → `gcode`,
  Source view → from `obj.kind`.

### Editing aids (Code mode)
Auto-indent on Enter; Tab / Shift+Tab indent / unindent the selected lines; Ctrl+D duplicate line; Alt+Up / Alt+Down
move lines; Ctrl+/ toggle comment with the lexer `comment_prefix` (G-code wraps in `( )`); Ctrl+wheel and
Ctrl+= / Ctrl+- / Ctrl+0 zoom; show whitespace; word wrap toggle; bracket match for `() {} []`. Each command is one
Undo step.

### Folding
Fold markers in the gutter (▸ / ▾) on lines that start a region. Click toggles. Ctrl+Shift+[ / ] fold / unfold at the
cursor; context menu Fold All / Unfold All. Edits inside a folded region unfold it. Fold state is not saved.

### Minimap
Width 80 px, off by default for documents under 300 lines. Density image rebuilt on a 300 ms debounce after
`contentsChange`, max 1 px per line and scaled down for long files. Viewport rectangle follows the scroll bar.

### Load performance
`CodeEdit.load(text)`: block signals, detach the highlighter, `document().setUndoRedoEnabled(False)`, `setPlainText`,
then enable undo again, reattach the highlighter (lazy), clear the modified flag. Fold scan and minimap image start
after the load on a timer.

### Document mode
- `RichTextToolbar` state follows the cursor char format and block alignment.
- Save of a `.FlatDoc` writes `toHtml()`; `.txt` writes plain text; `.pdf` keeps the current reportlab path.
- Default font / size change from the left panel applies to the whole document in one Undo step.
- Fix the `font_size_cb` order defect.

### Theme
`palette_for(app.options['global_theme'])`. The dark palette has its own colors; no hex inversion. The selection
color stays the Qt palette highlight in Code mode (no hard-coded yellow/black stylesheet).

### Preferences
New keys in `defaults.py` (`AppDefaults`), section `text_editor_*`: `font_family`, `font_size`, `wrap`,
`show_whitespace`, `minimap`, `tab_width` (Code mode, spaces). Read by `AppTextEditor`, written on toggle.
A Preferences UI panel is out of scope for this work.

## Phases

1. **Core**: `themes`, `highlighter` (port of the lazy logic with the current single rule set wrapped as
   `GCodeLexer` v0), `code_edit` (gutter, current line, bracket match, editing aids, zoom, wrap, whitespace,
   `goto_line`, `load`), `find_bar`, `status_bar`, facade rewiring. Remove `FCTextAreaLineNumber`.
2. **Lexers**: 5 lexers + `detect_language` + `language=` in the callers.
3. **Folding + minimap**.
4. **Document mode**: `rich_toolbar`, DocumentObject / DocumentObjectUI changes, `.FlatDoc` save fix.
   Runs in parallel with phases 1-3 (other files); the facade hook (`add_toolbar_row(widget)`) comes from phase 1.
5. **Caller cleanup**: `set_file_filter(flt)`, `set_save_handler(fn)`; remove the `clicked.disconnect()` rewiring.

## Testing

- Port `tests/test_lazy_highlight.py` to `CodeEdit` (same behaviors: lazy highlight, edit of a hidden line keeps
  colors, print highlights all lines, Replace All in one Undo step, Go to Line with wrap).
- New behavior tests in the same file (rename to `tests/test_text_editor.py`): find count + regex + whole word,
  find-all time on 200k matches under 0.5 s (generous bound under parallel load), wrap-around without a dialog,
  each editing aid = one Undo step, toggle comment per lexer, fold hides and shows the right blocks, lexer token
  colors on a sample line per language.
- `tests/test_object_construction.py` code-editor save tests stay. Add one `.FlatDoc` round trip keeping bold text.
- No tests of private attributes, call order or log text.

## Out of scope

Multi-cursor editing, a Preferences UI page for the editor, saved fold state, LSP, spell check, a Document-mode
table / image editor.
