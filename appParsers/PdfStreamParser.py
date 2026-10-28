# appParsers/PdfStreamParser.py
"""
PDF Stream Parser Module

Provides pluggable PDF content stream extraction with multiple backends:
- PurePythonPdfParser: Uses only standard library (zlib + re, default)
- PikePdfParser: Uses pikepdf library (optional, lazy import when enabled)

Configuration (via defaults.py):
    pdf_python_parser: True   -> Use pure-python parser (default, recommended)
    pdf_python_parser: False  -> Use pikepdf parser (requires pikepdf installation)

Usage:
    from appParsers.PdfStreamParser import get_pdf_parser
    
    # Get parser based on defaults.py configuration
    parser = get_pdf_parser(app.defaults)
    content = parser.extract_content_streams("file.pdf")
    
    # Or use convenience function
    from appParsers.PdfStreamParser import parse_pdf_content_streams
    content = parse_pdf_content_streams("file.pdf", app.defaults)

Why pure-python is default:
    - No external dependencies
    - Works with all Python 3.11+ installations
    - Latest pikepdf versions crash on import even with try/except
    - Lazy import of pikepdf only when explicitly enabled
    
Note: extract_page_streams() returns every page; extract_content_streams() the first one.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import base64
import logging
import re
import zlib

from appParsers.PdfText import PdfFont, cid_widths, parse_array, text_to_paths

log = logging.getLogger('base')


class PdfStreamParserBase(ABC):
    """
    Abstract base class for PDF content stream parsers.
    
    Extracts and decompresses PDF content streams into text format
    that can be parsed by ParsePDF.py
    """
    
    # The names of the Optional Content groups that are off by default and whose content was
    # skipped by the last extract_page_streams()
    hidden_layers = ()

    @abstractmethod
    def extract_page_streams(self, filename: str) -> list:
        """
        Extract and decompress the content streams of every page of a PDF file.

        Args:
            filename: Path to the PDF file

        Returns:
            One entry for each page, in page order: the decompressed content stream text
            (PDF operators as text, '' for a page without content) or the PdfParserError
            of a page that cannot be read. Each page keeps its own resources.

        Raises:
            PdfParserError: If the file as a whole cannot be read
        """
        pass

    def extract_content_streams(self, filename: str) -> str:
        """
        Extract and decompress the content streams of the first page.

        Raises:
            PdfParserError: If parsing fails or the first page has no content
        """
        first = self.extract_page_streams(filename)[0]
        if isinstance(first, PdfParserError):
            raise first
        if not first.strip():
            raise PdfParserError("No valid content streams found")
        return first

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this parser backend is available (dependencies installed).
        
        Returns:
            True if parser can be used, False otherwise
        """
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Return human-readable parser name."""
        pass


class PdfParserError(Exception):
    """Exception raised when PDF parsing fails."""
    pass


def off_groups(groups, on, off, base_off: bool) -> set:
    """The Optional Content groups that are hidden by the default configuration (/D).

    With /BaseState /OFF only the groups of the /ON array are visible; otherwise only the
    groups of the /OFF array are hidden.
    """
    return (set(groups) | set(off)) - set(on) if base_off else set(off)


def membership_visible(policy: str, hidden_flags: list) -> bool:
    """The visibility of a membership dictionary (OCMD) from the hidden flag of each of its groups."""
    if not hidden_flags:
        return True
    if policy == 'AllOn':
        return not any(hidden_flags)
    if policy == 'AnyOff':
        return any(hidden_flags)
    if policy == 'AllOff':
        return all(hidden_flags)
    return not all(hidden_flags)


class PurePythonPdfParser(PdfStreamParserBase):
    """
    PDF parser using only Python standard library.

    Resolves the /Contents references of each page object and decodes those streams
    only. Supports FlateDecode, ASCIIHexDecode and ASCII85Decode. Objects stored in
    compressed object streams (PDF 1.5+) are unpacked, Form XObjects drawn with ``Do``
    go inline into the page content and ExtGState line widths become ``w`` operators.

    This is the DEFAULT parser - no external dependencies required.

    Limitations:
    - Does not support encrypted PDFs
    - Does not support LZW or other compression filters
    - Does not support stream predictors (/DecodeParms)
    - May fail on complex PDF structures
    """

    # Limit for Form XObjects that draw other Form XObjects (also stops reference loops)
    MAX_FORM_DEPTH = 8
    
    # Regex to find PDF streams
    # Pattern: <<dictionary>> stream <compressed_data> endstream
    STREAM_PATTERN = re.compile(
        rb'<<.*?>>\s*stream\s*\r?\n(.*?)\s*\r?\n?\s*endstream',
        re.DOTALL
    )
    
    # Pattern to check for FlateDecode filter
    FLATE_DECODE_PATTERN = re.compile(rb'/FlateDecode')
    
    # Pattern to find BDC/EMC layer markers (PDF 1.2+)
    LAYER_PATTERN = re.compile(rb'BDC(.*?)EMC', re.DOTALL)

    # The marked-content operators; a string is matched too, so that it is skipped
    MARKED_CONTENT = re.compile(
        rb'\((?:\\.|[^\\()])*\)'
        rb'|/OC\s*/(?P<oc>[^\s/\[\]<>()%]+)\s*BDC\b'
        rb'|/[^\s/\[\]<>()%]+\s*(?:/[^\s/\[\]<>()%]+|<<.*?>>)?\s*(?P<other>BDC|BMC)\b'
        rb'|\b(?P<end>EMC)\b', re.DOTALL)

    # The Optional Content groups that are off (object references)
    _oc_off = frozenset()
    
    @property
    def name(self) -> str:
        return "pure-python"
    
    def is_available(self) -> bool:
        # Always available - uses only standard library
        return True
    
    def extract_page_streams(self, filename: str) -> list:
        """
        Extract the content streams of every page using only the standard library.

        The page object's ``/Contents`` references are resolved in order. Unrelated
        streams (fonts, images, metadata) are not concatenated into the geometry stream.

        Args:
            filename: Path to PDF file

        Returns:
            One entry for each page, in page order: the decompressed content stream
            text ('' for a page without content) or the PdfParserError of that page

        Raises:
            PdfParserError: If the file as a whole cannot be read
        """
        try:
            with open(filename, "rb") as f:
                pdf_data = f.read()

            return self._extract_pages_streams(pdf_data)

        except PdfParserError:
            raise
        except Exception as e:
            raise PdfParserError(f"pure-python parsing failed: {str(e)}")

    def _extract_pages_streams(self, pdf_data: bytes) -> list:
        """Resolve the page objects and decode the /Contents streams of each, in order."""
        if re.search(rb'/Encrypt\s+(\d+\s+\d+\s+R|<<)', pdf_data):
            raise PdfParserError(
                "Encrypted PDFs are not supported by the pure-python parser"
            )

        objects = self._parse_objects(pdf_data)
        if not objects:
            raise PdfParserError("No PDF objects found")

        if any(re.search(rb'/Encrypt\b', obj['dict']) for obj in objects.values()):
            raise PdfParserError(
                "Encrypted PDFs are not supported by the pure-python parser"
            )

        self._expand_object_streams(objects)
        self.hidden_layers = []
        self._load_optional_content(objects)

        page_refs = self._page_refs(objects)
        if not page_refs:
            raise PdfParserError("Could not locate the first page")

        pages = []
        for page_ref in page_refs:
            try:
                pages.append(self._page_streams(objects, page_ref))
            except PdfParserError as e:
                pages.append(e)
            except Exception as e:
                pages.append(PdfParserError(f"pure-python parsing failed: {str(e)}"))
        return pages

    def _page_streams(self, objects: dict, page_ref: tuple) -> str:
        """Decode the /Contents streams of one page in order; '' if it has none."""
        content_refs = self._resolve_content_refs(
            objects, self._content_refs(objects[page_ref]['dict'])
        )
        if not content_refs:
            return ''

        parts = []
        for ref in content_refs:
            content_obj = objects.get(ref)
            if content_obj is None or content_obj['stream'] is None:
                raise PdfParserError(
                    f"Missing content stream object {ref[0]} {ref[1]} R"
                )
            parts.append(self._decode_stream(content_obj['dict'], content_obj['stream']))

        content = self._expand_forms(objects, b'\n'.join(parts),
                                     self._page_resources(objects, page_ref))
        return content.decode('utf-8', errors='replace')

    def _expand_object_streams(self, objects: dict) -> bool:
        """Add the objects stored in compressed object streams (/Type /ObjStm).

        An object that is also stored directly in the file keeps the direct version.
        Returns True if the file has object streams.
        """
        found = False
        for ref, obj in list(objects.items()):
            if obj['stream'] is None or not re.search(rb'/Type\s*/ObjStm\b', obj['dict']):
                continue
            count = re.search(rb'/N\s+(\d+)', obj['dict'])
            first = re.search(rb'/First\s+(\d+)', obj['dict'])
            if not count or not first:
                raise PdfParserError(f"Malformed object stream {ref[0]} {ref[1]} R")

            found = True
            data =self._decode_stream(obj['dict'], obj['stream'])
            first = int(first.group(1))
            numbers = [int(value) for value in re.findall(rb'\d+', data[:first])]
            numbers = numbers[:2 * int(count.group(1))]
            entries = list(zip(numbers[0::2], numbers[1::2]))
            for index, (number, offset) in enumerate(entries):
                end = first + entries[index + 1][1] if index + 1 < len(entries) else len(data)
                objects.setdefault((number, 0),
                                   {'dict': data[first + offset:end].strip(), 'stream': None})
        return found

    @staticmethod
    def _dict_entry(dictionary: bytes, key: bytes) -> Optional[bytes]:
        """Return the raw value of a top-level key of a PDF dictionary, or None."""
        token = b'/' + key
        depth = 0
        i = 0
        while i < len(dictionary):
            if dictionary.startswith(b'<<', i):
                depth += 1
                i += 2
            elif dictionary.startswith(b'>>', i):
                depth -= 1
                i += 2
            elif dictionary[i:i + 1] == b'(':
                # skip a literal string; it can hold any bytes
                nesting = 0
                while i < len(dictionary):
                    char = dictionary[i:i + 1]
                    if char == b'\\':
                        i += 1
                    elif char == b'(':
                        nesting += 1
                    elif char == b')':
                        nesting -= 1
                        if nesting == 0:
                            break
                    i += 1
                i += 1
            elif depth == 1 and dictionary.startswith(token, i) and \
                    not re.match(rb'[^\s/\[\]<>()%]', dictionary[i + len(token):i + len(token) + 1]):
                return PurePythonPdfParser._value_at(dictionary, i + len(token))
            else:
                i += 1
        return None

    @staticmethod
    def _value_at(data: bytes, start: int) -> bytes:
        """Return the raw PDF value (dictionary, array, reference or token) at start."""
        start = re.compile(rb'\s*').match(data, start).end()
        for opener, closer in ((b'<<', b'>>'), (b'[', b']')):
            if data.startswith(opener, start):
                depth = 0
                i = start
                while i < len(data):
                    if data.startswith(opener, i):
                        depth += 1
                        i += len(opener)
                    elif data.startswith(closer, i):
                        depth -= 1
                        i += len(closer)
                        if depth == 0:
                            return data[start:i]
                    else:
                        i += 1
                return data[start:]
        match = re.compile(rb'\d+\s+\d+\s+R\b|[^\s/\[\]<>()%]*').match(data, start)
        if not match.group(0) and data[start:start + 1] == b'/':
            match = re.compile(rb'/[^\s/\[\]<>()%]*').match(data, start)
        return match.group(0)

    @staticmethod
    def _resolve(objects: dict, value: Optional[bytes]) -> Optional[bytes]:
        """Return the dictionary of an indirect reference, or the direct value."""
        if value is None:
            return None
        ref = re.fullmatch(rb'(\d+)\s+(\d+)\s+R', value)
        if ref:
            obj = objects.get((int(ref.group(1)), int(ref.group(2))))
            return obj['dict'] if obj is not None else None
        return value

    @classmethod
    def _page_resources(cls, objects: dict, page_ref: tuple) -> Optional[bytes]:
        """Return the /Resources dictionary of a page, inherited from the page tree if needed."""
        ref, seen = page_ref, set()
        while ref in objects and ref not in seen:
            seen.add(ref)
            dictionary = objects[ref]['dict']
            resources = cls._resolve(objects, cls._dict_entry(dictionary, b'Resources'))
            if resources is not None:
                return resources
            parent = re.fullmatch(rb'(\d+)\s+(\d+)\s+R',
                                  cls._dict_entry(dictionary, b'Parent') or b'')
            if not parent:
                break
            ref = (int(parent.group(1)), int(parent.group(2)))
        return None

    def _stream_bytes(self, objects: dict, value: Optional[bytes]) -> Optional[bytes]:
        """Return the decoded data of the stream that an indirect reference points to."""
        ref = re.fullmatch(rb'(\d+)\s+(\d+)\s+R', value or b'')
        obj = objects.get((int(ref.group(1)), int(ref.group(2)))) if ref else None
        if obj is None or obj['stream'] is None:
            return None
        return self._decode_stream(obj['dict'], obj['stream'])

    def _font_getter(self, objects: dict, resources: bytes):
        """Return get_font(name) for the /Font resources, for PdfText.text_to_paths."""
        fonts = self._resolve(objects, self._dict_entry(resources, b'Font'))
        cache = {}

        def get_font(name: bytes):
            if name not in cache:
                font_dict = self._resolve(objects, self._dict_entry(fonts, name)) if fonts else None
                try:
                    cache[name] = self._load_font(objects, font_dict) if font_dict else None
                except Exception as e:
                    log.warning(f"PDF text: font /{name.decode('latin-1')} cannot be read: {e}")
                    cache[name] = None
            return cache[name]
        return get_font

    def _font_program(self, objects: dict, descriptor: Optional[bytes]) -> Optional[bytes]:
        """Return the embedded TrueType/OpenType program of a font descriptor."""
        if not descriptor:
            return None
        program = self._dict_entry(descriptor, b'FontFile2')
        if program is not None:
            return self._stream_bytes(objects, program)
        program = self._dict_entry(descriptor, b'FontFile3')
        program_ref = re.fullmatch(rb'(\d+)\s+(\d+)\s+R', program or b'')
        if program_ref:
            obj = objects.get((int(program_ref.group(1)), int(program_ref.group(2))))
            if obj is not None and self._dict_entry(obj['dict'], b'Subtype') == b'/OpenType':
                return self._stream_bytes(objects, program)
        return None

    def _load_font(self, objects: dict, font_dict: bytes):
        """Build a PdfFont from a Type0 (Identity) or a simple TrueType font dictionary."""
        subtype = self._dict_entry(font_dict, b'Subtype')
        if subtype == b'/Type0':
            if self._dict_entry(font_dict, b'Encoding') not in (b'/Identity-H', b'/Identity-V'):
                return None
            # the first element of the array: a direct dictionary or a reference
            descendants = self._resolve(objects, self._dict_entry(font_dict, b'DescendantFonts')) or b''
            first = re.match(rb'\s*\[\s*(<<|\d+\s+\d+\s+R)', descendants)
            if not first:
                return None
            cid_font = self._value_at(descendants, first.start(1)) if first.group(1) == b'<<' \
                else self._resolve(objects, re.sub(rb'\s+', b' ', first.group(1)))
            if not cid_font:
                return None
            program = self._font_program(
                objects, self._resolve(objects, self._dict_entry(cid_font, b'FontDescriptor')))
            if program is None:
                return None
            widths = cid_widths(parse_array(
                self._resolve(objects, self._dict_entry(cid_font, b'W')) or b''))
            default_width = self._dict_entry(cid_font, b'DW')
            return PdfFont(program, True, widths,
                           float(default_width) if default_width else 1000.0,
                           self._stream_bytes(objects, self._dict_entry(cid_font, b'CIDToGIDMap')))
        if subtype == b'/TrueType':
            program = self._font_program(
                objects, self._resolve(objects, self._dict_entry(font_dict, b'FontDescriptor')))
            if program is None:
                return None
            first = self._dict_entry(font_dict, b'FirstChar')
            first = int(float(first)) if first else 0
            widths = parse_array(self._resolve(objects, self._dict_entry(font_dict, b'Widths')) or b'')
            return PdfFont(program, False, {first + index: float(width)
                                            for index, width in enumerate(widths)
                                            if not isinstance(width, list)},
                           simple_encoding=True)
        return None

    @classmethod
    def _refs(cls, objects: dict, value: Optional[bytes]) -> list:
        """The references of an array, given directly or as a reference to an array object."""
        ref = re.fullmatch(rb'(\d+)\s+(\d+)\s+R', value or b'')
        if ref and objects.get((int(ref.group(1)), int(ref.group(2)))) is not None:
            array = objects[(int(ref.group(1)), int(ref.group(2)))]['dict']
            if array.lstrip().startswith(b'['):
                value = array
        return [(int(number), int(generation))
                for number, generation in re.findall(rb'(\d+)\s+(\d+)\s+R', value or b'')]

    def _load_optional_content(self, objects: dict) -> None:
        """Find the Optional Content groups that are off by default (catalog /OCProperties)."""
        self._oc_off = frozenset()
        try:
            for obj in objects.values():
                if re.search(rb'/Type\s*/Catalog\b', obj['dict']):
                    properties = self._resolve(objects, self._dict_entry(obj['dict'], b'OCProperties'))
                    if properties:
                        config = self._resolve(objects, self._dict_entry(properties, b'D')) or b''
                        self._oc_off = frozenset(off_groups(
                            self._refs(objects, self._dict_entry(properties, b'OCGs')),
                            self._refs(objects, self._dict_entry(config, b'ON')),
                            self._refs(objects, self._dict_entry(config, b'OFF')),
                            self._dict_entry(config, b'BaseState') == b'/OFF'))
                    break
        except Exception as e:
            log.warning(f"PDF layers cannot be read, all content stays visible: {e}")

    def _group_name(self, objects: dict, ref: tuple) -> str:
        """The /Name of an Optional Content group."""
        match = re.search(rb'/Name\s*(?:\(((?:\\.|[^\\()])*)\)|<([0-9A-Fa-f\s]*)>)',
                          objects.get(ref, {}).get('dict', b''))
        if match:
            try:
                raw = match.group(1) if match.group(1) is not None else \
                    bytes.fromhex(re.sub(rb'\s', b'', match.group(2)).decode())
                raw = re.sub(rb'\\(.)', rb'\1', raw)
                return raw[2:].decode('utf-16-be') if raw[:2] == b'\xfe\xff' else raw.decode('latin-1')
            except ValueError:
                pass
        return f"OCG {ref[0]}"

    def _hidden_groups(self, objects: dict, value: Optional[bytes]) -> list:
        """The names of the hidden groups that hide content marked with this /OC value.

        value is an Optional Content group or a membership dictionary (OCMD), as a reference
        or directly. An empty list means visible, also for a value that cannot be resolved.
        """
        if not value or not self._oc_off:
            return []
        ref = re.fullmatch(rb'(\d+)\s+(\d+)\s+R', value)
        members = [(int(ref.group(1)), int(ref.group(2)))] if ref else []
        if members and members[0] not in self._oc_off:
            dictionary = self._resolve(objects, value)
            if not dictionary or self._dict_entry(dictionary, b'Type') != b'/OCMD':
                return []
            members = self._refs(objects, self._dict_entry(dictionary, b'OCGs'))
            policy = (self._dict_entry(dictionary, b'P') or b'/AnyOn').lstrip(b'/').decode('latin-1')
            if membership_visible(policy, [member in self._oc_off for member in members]):
                return []
        hidden = [self._group_name(objects, member) for member in members
                  if member in self._oc_off] or [self._group_name(objects, member) for member in members]
        self.hidden_layers = [*self.hidden_layers, *(name for name in dict.fromkeys(hidden)
                                                     if name not in self.hidden_layers)]
        return hidden

    def _hide_marked_content(self, objects: dict, content: bytes, resources: bytes) -> bytes:
        """Remove the content of the hidden Optional Content groups: /OC /Name BDC ... EMC.

        The markers nest; the content is hidden while any open /OC marker is hidden.
        """
        if not self._oc_off:
            return content
        properties = self._resolve(objects, self._dict_entry(resources, b'Properties'))
        if not properties:
            return content

        kept, position, hidden_levels, hidden = [], 0, [], 0
        for match in self.MARKED_CONTENT.finditer(content):
            if match.group('end'):
                if not hidden_levels:
                    continue
                was_hidden, flag = hidden > 0, hidden_levels.pop()
                hidden -= flag
            elif match.group('oc') or match.group('other'):
                was_hidden = hidden > 0
                flag = 1 if match.group('oc') and self._hidden_groups(
                    objects, self._dict_entry(properties, match.group('oc'))) else 0
                hidden_levels.append(flag)
                hidden += flag
            else:
                continue
            if hidden and not was_hidden:
                kept.append(content[position:match.start()])
            elif was_hidden and not hidden:
                position = match.end()
        if not hidden:
            kept.append(content[position:])
        return b''.join(kept)

    def _expand_forms(self, objects: dict, content: bytes, resources: Optional[bytes],
                      depth: int = 0) -> bytes:
        """Resolve the resource operators that ParsePDF cannot read from the resources.

        - 'Do' of a Form XObject: the form content goes inline, wrapped in
          'q <Matrix> cm ... Q'. Image XObjects stay as 'Do', so ParsePDF still
          reports them as unsupported.
        - 'gs' of an ExtGState with /LW: a '<LW> w' follows it, because some PDFs set
          the line width only in the ExtGState. Without it, all strokes have width 0.
        """
        if not resources:
            return content
        content = self._hide_marked_content(objects, content, resources)
        content = text_to_paths(content, self._font_getter(objects, resources))
        xobject_dict = self._resolve(objects, self._dict_entry(resources, b'XObject'))
        ext_gstate_dict = self._resolve(objects, self._dict_entry(resources, b'ExtGState'))
        if not xobject_dict and not ext_gstate_dict:
            return content
        xobjects = {name: (int(number), int(generation))
                    for name, number, generation in re.findall(
                        rb'/([^\s/\[\]<>()%]+)\s+(\d+)\s+(\d+)\s+R', xobject_dict or b'')}

        def line_width(match):
            state = self._resolve(objects, self._dict_entry(ext_gstate_dict, match.group(1))) \
                if ext_gstate_dict else None
            width = self._dict_entry(state, b'LW') if state else None
            if width is None or not re.fullmatch(rb'[+-]?(\d+\.?\d*|\.\d+)', width):
                return match.group(0)
            return match.group(0) + b' ' + width + b' w'

        def inline_form(match):
            if match.group(2) == b'gs':
                return line_width(match)
            obj = objects.get(xobjects.get(match.group(1)))
            if obj is not None and self._hidden_groups(objects, self._dict_entry(obj['dict'], b'OC')):
                return b''
            if obj is None or obj['stream'] is None or depth >= self.MAX_FORM_DEPTH or \
                    self._dict_entry(obj['dict'], b'Subtype') != b'/Form':
                return match.group(0)
            form = self._decode_stream(obj['dict'], obj['stream'])
            form_resources = self._resolve(objects, self._dict_entry(obj['dict'], b'Resources'))
            form = self._expand_forms(objects, form, form_resources or resources, depth + 1)
            matrix = self._dict_entry(obj['dict'], b'Matrix')
            transform = matrix.strip(b'[]').strip() + b' cm\n' if matrix else b''
            return b'q ' + transform + form + b'\nQ'

        return re.sub(rb'/([^\s/\[\]<>()%]+)\s+(Do|gs)\b', inline_form, content)

    @classmethod
    def _resolve_content_refs(cls, objects: dict, refs: list) -> list:
        """Expand an indirect /Contents array object into its stream references."""
        resolved = []
        for ref in refs:
            obj = objects.get(ref)
            if obj is not None and obj['stream'] is None:
                nested = cls._content_refs(obj['dict'])
                if not nested:
                    array = re.search(rb'\[(.*?)\]', obj['dict'], re.DOTALL)
                    if array:
                        nested = [(int(number), int(generation))
                                  for number, generation in re.findall(
                                      rb'(\d+)\s+(\d+)\s+R', array.group(1))]
                if nested:
                    resolved.extend(cls._resolve_content_refs(objects, nested))
                    continue
            resolved.append(ref)
        return resolved

    @staticmethod
    def _parse_objects(pdf_data: bytes) -> Dict[tuple, Dict[str, Any]]:
        """Parse indirect objects, including their dictionaries and raw stream bytes.

        Stream data is bounded by the declared /Length (direct or indirect) instead of
        the first ``endstream`` / ``endobj`` token: both words are legal inside an
        uncompressed content stream. The token search is only a fallback for a missing
        or malformed length.
        """
        objects = {}
        header_re = re.compile(rb'(\d+)\s+(\d+)\s+obj\b')
        stream_re = re.compile(rb'>>\s*stream[ \t]*(?:\r\n|\r|\n)')
        position = 0

        while True:
            match = header_re.search(pdf_data, position)
            if not match:
                break

            number = int(match.group(1))
            generation = int(match.group(2))
            body_start = match.end()
            endobj = pdf_data.find(b'endobj', body_start)
            stream_match = stream_re.search(pdf_data, body_start)

            if stream_match is not None and (endobj < 0 or stream_match.start() < endobj):
                dictionary = pdf_data[body_start:stream_match.start() + 2]
                data_start = stream_match.end()
                length = PurePythonPdfParser._stream_length(pdf_data, dictionary)
                next_position = None
                stream_data = None

                if length is not None and 0 <= length <= len(pdf_data) - data_start:
                    candidate_end = data_start + length
                    if re.match(rb'\s*endstream\b',
                                pdf_data[candidate_end:candidate_end + 32]):
                        stream_data = pdf_data[data_start:candidate_end]
                        next_position = candidate_end

                if next_position is None:
                    endstream = pdf_data.find(b'endstream', data_start)
                    if endstream < 0:
                        endstream = endobj if endobj >= 0 else len(pdf_data)
                    stream_data = pdf_data[data_start:endstream]
                    if stream_data.endswith(b'\r\n'):
                        stream_data = stream_data[:-2]
                    elif stream_data.endswith((b'\n', b'\r')):
                        stream_data = stream_data[:-1]
                    next_position = endstream + len(b'endstream')

                end = pdf_data.find(b'endobj', next_position)
                if end < 0:
                    end = next_position
            else:
                if endobj < 0:
                    break
                dictionary = pdf_data[body_start:endobj]
                stream_data = None
                end = endobj

            objects[(number, generation)] = {'dict': dictionary, 'stream': stream_data}
            position = end + len(b'endobj')

        return objects

    @staticmethod
    def _stream_length(pdf_data: bytes, dictionary: bytes) -> Optional[int]:
        """Return the declared stream length, resolving an indirect /Length object."""
        match = re.search(rb'/Length\s+(\d+)\s+(\d+)\s+R\b', dictionary)
        if match:
            length_ref = re.search(
                rb'(?<![\d])%d\s+%d\s+obj\b\s*([+-]?\d+)' %
                (int(match.group(1)), int(match.group(2))),
                pdf_data
            )
            return int(length_ref.group(1)) if length_ref else None

        match = re.search(rb'/Length\s+([+-]?\d+)(?!\s+\d+\s+R)\b', dictionary)
        return int(match.group(1)) if match else None

    @staticmethod
    def _walk_pages(objects: dict, ref: tuple, seen: set, pages: list) -> None:
        """Depth-first walk of the page tree; append the /Page references in page order."""
        if ref in seen or ref not in objects:
            return
        seen.add(ref)

        dictionary = objects[ref]['dict']
        if re.search(rb'/Type\s*/Page\b', dictionary):
            pages.append(ref)
        elif re.search(rb'/Type\s*/Pages\b', dictionary):
            kids = re.search(rb'/Kids\s*\[(.*?)\]', dictionary, re.DOTALL)
            if kids:
                for number, generation in re.findall(rb'(\d+)\s+(\d+)\s+R', kids.group(1)):
                    PurePythonPdfParser._walk_pages(
                        objects, (int(number), int(generation)), seen, pages
                    )

    @classmethod
    def _page_refs(cls, objects: dict) -> list:
        """Find all pages through the catalog/page tree, with a file-order fallback."""
        for ref, obj in objects.items():
            if re.search(rb'/Type\s*/Catalog\b', obj['dict']):
                root = re.search(rb'/Pages\s+(\d+)\s+(\d+)\s+R', obj['dict'])
                if root:
                    pages = []
                    cls._walk_pages(
                        objects, (int(root.group(1)), int(root.group(2))), set(), pages
                    )
                    if pages:
                        return pages
                break

        return sorted(ref for ref, obj in objects.items()
                      if re.search(rb'/Type\s*/Page\b', obj['dict']))

    @staticmethod
    def _content_refs(dictionary: bytes) -> list:
        """Return the /Contents references of a page in array order."""
        match = re.search(
            rb'/Contents\s*(\[(.*?)\]|(\d+)\s+(\d+)\s+R)', dictionary, re.DOTALL
        )
        if not match:
            return []
        if match.group(2) is not None:
            return [(int(number), int(generation))
                    for number, generation in re.findall(rb'(\d+)\s+(\d+)\s+R', match.group(2))]
        return [(int(match.group(3)), int(match.group(4)))]

    @staticmethod
    def _stream_filters(dictionary: bytes) -> list:
        match = re.search(rb'/Filter\s*(\[[^\]]*\]|/\w+)', dictionary)
        if not match:
            if re.search(rb'/Filter\b', dictionary):
                raise PdfParserError("Unsupported indirect PDF filter reference")
            return []
        return [name.decode('ascii') for name in re.findall(rb'/(\w+)', match.group(1))]

    @staticmethod
    def _decode_ascii_hex(data: bytes) -> bytes:
        data = data.split(b'>', 1)[0]
        data = re.sub(rb'\s+', b'', data)
        if len(data) % 2:
            data += b'0'
        try:
            return bytes.fromhex(data.decode('ascii'))
        except ValueError as exc:
            raise PdfParserError(f"ASCIIHexDecode failed: {exc}")

    @staticmethod
    def _decode_ascii85(data: bytes) -> bytes:
        data = data.strip()
        if data.startswith(b'<~'):
            data = data[2:]
        if data.endswith(b'~>'):
            data = data[:-2]
        try:
            return base64.a85decode(re.sub(rb'\s+', b'', data))
        except ValueError as exc:
            raise PdfParserError(f"ASCII85Decode failed: {exc}")

    def _decode_stream(self, dictionary: bytes, stream_data: bytes) -> bytes:
        """Apply the stream's declared filters. Unknown filters fail loudly."""
        if re.search(rb'/DecodeParms', dictionary):
            raise PdfParserError("Unsupported PDF stream parameter: /DecodeParms")

        data = stream_data
        for filter_name in self._stream_filters(dictionary):
            if filter_name == 'FlateDecode':
                decoded = self._decompress_stream(data)
                if decoded is None:
                    raise PdfParserError("FlateDecode decompression failed")
                data = decoded
            elif filter_name == 'ASCIIHexDecode':
                data = self._decode_ascii_hex(data)
            elif filter_name == 'ASCII85Decode':
                data = self._decode_ascii85(data)
            else:
                raise PdfParserError(f"Unsupported PDF filter: {filter_name}")

        return data

    def _extract_all_streams(self, pdf_data: bytes) -> str:
        """
        Extract and decompress all FlateDecode streams.
        
        Args:
            pdf_data: Raw PDF file data
            
        Returns:
            Concatenated decompressed stream content
        """
        output_parts = []
        
        for match in self.STREAM_PATTERN.finditer(pdf_data):
            stream_dict = pdf_data[match.start():match.start() + match.group(0).find(b'stream')]
            compressed_data = match.group(1)
            
            # Only process FlateDecode streams
            if not self.FLATE_DECODE_PATTERN.search(stream_dict):
                continue
            
            try:
                decompressed = self._decompress_stream(compressed_data)
                if decompressed:
                    try:
                        text = decompressed.decode('utf-8', errors='replace')
                    except Exception:
                        text = decompressed.decode('latin-1', errors='replace')
                    output_parts.append(text)
            except Exception as e:
                log.debug(f"Stream decompression failed: {e}")
                continue
        
        return '\n'.join(output_parts)
    
    def _decompress_if_needed(self, data: bytes) -> Optional[bytes]:
        """Try to decompress data if it's compressed."""
        # Try direct decode first
        try:
            data.decode('utf-8')
            return data  # Already decompressed
        except UnicodeDecodeError:
            pass
        
        # Try decompression
        return self._decompress_stream(data)
    
    def _decompress_stream(self, compressed_data: bytes) -> Optional[bytes]:
        """
        Decompress FlateDecode stream using zlib.
        
        Args:
            compressed_data: Raw compressed stream data
            
        Returns:
            Decompressed data or None if decompression fails
        """
        # Try different zlib decompression strategies
        # -15 = raw deflate (no zlib header) - most common for PDFs
        try:
            return zlib.decompress(compressed_data, -15)
        except zlib.error:
            pass
        
        # Try with standard zlib header
        try:
            return zlib.decompress(compressed_data)
        except zlib.error:
            pass
        
        # Try with auto-detect header (15 + 32)
        try:
            return zlib.decompress(compressed_data, 15 + 32)
        except zlib.error:
            pass
        
        # All strategies failed - data might be corrupted or use different filter
        log.debug("All zlib decompression strategies failed")
        return None


class PikePdfParser(PdfStreamParserBase):
    """
    PDF parser using pikepdf library.
    
    Provides full PDF support including:
    - Multiple compression filters
    - Object streams
    - Encrypted PDFs (with password)
    - Complex PDF structures
    
    IMPORTANT: pikepdf is imported LAZY (only when this class is instantiated).
    This avoids import crashes on systems with incompatible pikepdf versions.
    
    Note: Only extracts content from the first page (existing FlatCAM behavior).
    """
    
    def __init__(self):
        """
        Lazy import of pikepdf - only import when actually creating instance.
        
        This prevents import-time crashes from breaking the application.
        """
        self._pikepdf = None
        self._available = None
        self.unsupported_features = set()
        self.hidden_layers = []
        self._oc_off = {}
        self._try_import()
    
    def _try_import(self) -> None:
        """
        Attempt lazy import of pikepdf.
        
        Sets self._available to True if import succeeds, False otherwise.
        """
        if self._available is not None:
            return  # Already tried
        
        try:
            # LAZY IMPORT - only happens when PikePdfParser is instantiated
            import pikepdf
            from pikepdf import Pdf, parse_content_stream
            self._pikepdf = {
                'Pdf': Pdf,
                'parse_content_stream': parse_content_stream,
                'Operator': pikepdf.Operator,
                'Array': pikepdf.Array,
                # pikepdf versions without it fall back to unparse() of each operand
                'unparse_content_stream': getattr(pikepdf, 'unparse_content_stream', None),
            }
            self._available = True
            log.debug("pikepdf imported successfully (lazy import)")
        except Exception as e:
            log.warning(f"pikepdf lazy import failed: {e}")
            self._pikepdf = None
            self._available = False
    
    @property
    def name(self) -> str:
        return "pikepdf"
    
    def is_available(self) -> bool:
        """Check if pikepdf was successfully imported."""
        return self._available is True
    
    def extract_page_streams(self, filename: str) -> list:
        """
        Extract the content streams of every page using pikepdf.

        Args:
            filename: Path to PDF file

        Returns:
            One entry for each page, in page order: the content stream text ('' for a
            page without content) or the PdfParserError of that page

        Raises:
            PdfParserError: If pikepdf is not available or the file cannot be read
        """
        if not self.is_available():
            raise PdfParserError(
                "pikepdf is not available. "
                "Install with: pip install pikepdf>=2.0, "
                "or set pdf_python_parser=True in defaults.py to use pure-python parser"
            )

        self.unsupported_features = set()
        self.hidden_layers = []

        try:
            Pdf = self._pikepdf['Pdf']
            parse_content_stream = self._pikepdf['parse_content_stream']
            
            with open(filename, "rb") as f:
                pdf = Pdf.open(f)

                if not len(pdf.pages):
                    raise PdfParserError("Could not locate the first page")

                self._load_optional_content(pdf)
                pages = []
                for page in pdf.pages:
                    try:
                        pages.append(self._content_lines(
                            page, parse_content_stream, self._page_resources(page), 0
                        ))
                    except Exception as e:
                        pages.append(PdfParserError(f"pikepdf parsing failed: {str(e)}"))
                return pages

        except Exception as e:
            raise PdfParserError(f"pikepdf parsing failed: {str(e)}")

    def _load_optional_content(self, pdf) -> None:
        """Find the Optional Content groups that are off by default (catalog /OCProperties).

        self._oc_off maps the object number and generation of each of them to its name.
        """
        self._oc_off = {}
        try:
            properties = pdf.Root.get('/OCProperties')
            if properties is None:
                return
            config = properties.get('/D')
            config = config if config is not None else {}
            groups = list(properties.get('/OCGs', []))
            on = list(config.get('/ON', []))
            off = list(config.get('/OFF', []))
            hidden = off_groups([group.objgen for group in groups], [group.objgen for group in on],
                                [group.objgen for group in off], str(config.get('/BaseState')) == '/OFF')
            for group in groups + on + off:
                if group.objgen in hidden:
                    self._oc_off[group.objgen] = str(group.get('/Name', f"OCG {group.objgen[0]}"))
        except Exception as e:
            log.warning(f"PDF layers cannot be read, all content stays visible: {e}")
            self._oc_off = {}

    def _hidden_groups(self, value) -> list:
        """The names of the hidden groups that hide content marked with this /OC value (group or OCMD).

        An empty list means visible, also for a value that cannot be resolved.
        """
        if value is None or not self._oc_off:
            return []
        try:
            if value.objgen in self._oc_off:
                members = [value]
            elif str(value.get('/Type')) == '/OCMD':
                groups = value.get('/OCGs')
                members = [] if groups is None else \
                    list(groups) if isinstance(groups, self._pikepdf['Array']) else [groups]
                policy = str(value.get('/P', '/AnyOn')).lstrip('/')
                if membership_visible(policy, [member.objgen in self._oc_off for member in members]):
                    return []
            else:
                return []
            hidden = [self._oc_off.get(member.objgen, str(member.get('/Name'))) for member in members
                      if member.objgen in self._oc_off] or [str(member.get('/Name')) for member in members]
        except Exception:
            return []
        self.hidden_layers = [*self.hidden_layers, *(name for name in dict.fromkeys(hidden)
                                                     if name not in self.hidden_layers)]
        return hidden

    @staticmethod
    def _page_resources(page):
        """Return the /Resources of a page, inherited from the page tree if needed."""
        resources = getattr(page, 'resources', None)
        if resources is not None:
            return resources
        node = page.obj if hasattr(page, 'obj') else page
        for _ in range(64):
            if node is None:
                break
            if '/Resources' in node:
                return node['/Resources']
            node = node.get('/Parent')
        return None

    def _content_lines(self, owner, parse_content_stream, resources, depth: int) -> str:
        """Write the content of a page or Form XObject as text, one operator for each line.

        Form XObjects drawn with 'Do' go inline, wrapped in 'q <Matrix> cm ... Q'.
        After 'gs' of an ExtGState with /LW, a '<LW> w' follows, because ParsePDF
        reads the line width only from 'w'.
        """
        text = ''
        # one flag for each open marked-content level: is it a hidden Optional Content group
        marked, hidden = [], 0
        for operands, command in parse_content_stream(owner):
            command = str(command)
            if command in ('BDC', 'BMC'):
                flag = 1 if command == 'BDC' and len(operands) >= 2 and str(operands[0]) == '/OC' and \
                    self._hidden_groups(self._resource(resources, '/Properties', operands[1])) else 0
                marked.append(flag)
                hidden += flag
                continue
            if command == 'EMC':
                if marked:
                    hidden -= marked.pop()
                continue
            if hidden:
                continue
            if command == 'Do' and operands:
                form = self._resource(resources, '/XObject', operands[0])
                if form is not None and self._hidden_groups(form.get('/OC')):
                    continue
                if form is not None and str(form.get('/Subtype')) == '/Form' and \
                        depth < PurePythonPdfParser.MAX_FORM_DEPTH:
                    matrix = form.get('/Matrix')
                    text += 'q\n'
                    if matrix is not None:
                        text += ' '.join(str(float(value)) for value in matrix) + ' cm\n'
                    text += self._content_lines(form, parse_content_stream,
                                                form.get('/Resources', resources), depth + 1)
                    text += 'Q\n'
                    continue
                self.unsupported_features.add('XObject')

            text += self._unparse(operands, command) + '\n'

            if command == 'gs' and operands:
                state = self._resource(resources, '/ExtGState', operands[0])
                width = state.get('/LW') if state is not None else None
                if width is not None:
                    try:
                        text += f'{float(width)} w\n'
                    except (TypeError, ValueError):
                        pass
        # the forms inlined above are already converted; they hold no text objects
        return text_to_paths(text.encode('latin-1'),
                             self._font_getter(resources)).decode('latin-1')

    def _font_getter(self, resources):
        """Return get_font(name) for the /Font resources, for PdfText.text_to_paths."""
        cache = {}

        def get_font(name: bytes):
            if name not in cache:
                font = self._resource(resources, '/Font', '/' + name.decode('latin-1'))
                try:
                    cache[name] = self._load_font(font) if font is not None else None
                except Exception as e:
                    log.warning(f"PDF text: font /{name.decode('latin-1')} cannot be read: {e}")
                    cache[name] = None
            return cache[name]
        return get_font

    @staticmethod
    def _plain_list(array) -> list:
        """Convert a pikepdf array of numbers and nested arrays into Python lists."""
        result = []
        for item in array:
            try:
                result.append(float(item))
            except (TypeError, ValueError):
                result.append(PikePdfParser._plain_list(item))
        return result

    @staticmethod
    def _font_program(descriptor):
        if descriptor is None:
            return None
        if '/FontFile2' in descriptor:
            return descriptor['/FontFile2'].read_bytes()
        program = descriptor.get('/FontFile3')
        if program is not None and str(program.get('/Subtype')) == '/OpenType':
            return program.read_bytes()
        return None

    def _load_font(self, font):
        """Build a PdfFont from a Type0 (Identity) or a simple TrueType font dictionary."""
        subtype = str(font.get('/Subtype'))
        if subtype == '/Type0':
            if str(font.get('/Encoding')) not in ('/Identity-H', '/Identity-V'):
                return None
            cid_font = font['/DescendantFonts'][0]
            program = self._font_program(cid_font.get('/FontDescriptor'))
            if program is None:
                return None
            # /CIDToGIDMap is a stream, or the name /Identity (no map)
            cid_to_gid = cid_font.get('/CIDToGIDMap')
            try:
                cid_to_gid = cid_to_gid.read_bytes() if cid_to_gid is not None else None
            except Exception:
                cid_to_gid = None
            return PdfFont(program, True, cid_widths(self._plain_list(cid_font.get('/W', []))),
                           float(cid_font.get('/DW', 1000)), cid_to_gid)
        if subtype == '/TrueType':
            program = self._font_program(font.get('/FontDescriptor'))
            if program is None:
                return None
            first = int(font.get('/FirstChar', 0))
            widths = self._plain_list(font.get('/Widths', []))
            return PdfFont(program, False, {first + index: width for index, width in enumerate(widths)},
                           simple_encoding=True)
        return None

    def _unparse(self, operands, command: str) -> str:
        """Write one instruction in PDF syntax.

        str() of a pikepdf operand is not PDF syntax: a dictionary becomes
        'pikepdf.Dictionary({...})' and a string becomes its raw characters.
        """
        unparse = self._pikepdf.get('unparse_content_stream')
        if unparse is not None:
            try:
                return unparse([(operands, self._pikepdf['Operator'](command))]).decode('latin-1')
            except Exception:
                pass
        line = ''
        for op in operands:
            try:
                line += (op.unparse().decode('latin-1') if hasattr(op, 'unparse') else str(op)) + ' '
            except Exception:
                # Skip operands that can't be converted to string
                pass
        return line + command

    @staticmethod
    def _resource(resources, category: str, name):
        """Return the named resource of a category (/XObject, /ExtGState), or None."""
        try:
            return resources[category][str(name)]
        except Exception:
            return None


def get_pdf_parser(app_defaults: Optional[Dict[str, Any]] = None) -> PdfStreamParserBase:
    """
    Factory function to get PDF parser based on configuration.
    
    Args:
        app_defaults: Application defaults dict (from defaults.py).
                     If None, uses pure-python parser as safe default.
    
    Returns:
        PdfStreamParserBase instance
        
    Raises:
        PdfParserError: If selected parser is not available
    """
    # Default to pure-python (safe, no dependencies)
    use_pure_python = True
    
    if app_defaults is not None:
        # Check configuration: pdf_python_parser=True means use pure-python
        use_pure_python = app_defaults.get('pdf_python_parser', True)
    
    if use_pure_python:
        log.debug("Using pure-python PDF parser (pdf_python_parser=True)")
        return PurePythonPdfParser()
    else:
        # User explicitly requested pikepdf
        log.debug("Using pikepdf PDF parser (pdf_python_parser=False)")
        parser = PikePdfParser()
        
        if not parser.is_available():
            log.warning(
                "pikepdf parser requested but not available. "
                "Falling back to pure-python parser. "
                "Set pdf_python_parser=True in defaults.py to suppress this warning."
            )
            return PurePythonPdfParser()
        
        return parser


def get_pdf_parser_by_name(name: str) -> PdfStreamParserBase:
    """
    Factory function to get PDF parser by explicit name.
    
    Args:
        name: Parser name ('pure-python' or 'pikepdf')
    
    Returns:
        PdfStreamParserBase instance
    """
    if name == 'pure-python':
        return PurePythonPdfParser()
    elif name == 'pikepdf':
        return PikePdfParser()
    else:
        raise PdfParserError(f"Unknown parser name: {name}")


def parse_pdf_content_streams(
    filename: str,
    app_defaults: Optional[Dict[str, Any]] = None
) -> str:
    """
    Extract content streams from a PDF file.
    
    Only extracts the first page; use get_pdf_parser().extract_page_streams() for all.

    Args:
        filename: Path to PDF file
        app_defaults: Application defaults dict

    Returns:
        Decompressed content stream text
        
    Raises:
        PdfParserError: If parsing fails
    """
    parser = get_pdf_parser(app_defaults)
    return parser.extract_content_streams(filename)


# Exports
__all__ = [
    'PdfStreamParserBase',
    'PdfParserError',
    'PurePythonPdfParser',
    'PikePdfParser',
    'get_pdf_parser',
    'get_pdf_parser_by_name',
    'parse_pdf_content_streams',
]
