#!/usr/bin/env python3
"""
Automatically convert {% if lang %} blocks to {{ _() }} calls in templates.
Updates .po files with extracted strings.
"""

import os
import re
import sys
from pathlib import Path
from collections import defaultdict

# Paths
PROJECT_ROOT = Path(__file__).parent
TEMPLATES_DIR = PROJECT_ROOT / 'app' / 'templates'
TRANSLATIONS_DIR = PROJECT_ROOT / 'app' / 'translations'
NL_PO_FILE = TRANSLATIONS_DIR / 'nl' / 'LC_MESSAGES' / 'messages.po'
EN_PO_FILE = TRANSLATIONS_DIR / 'en' / 'LC_MESSAGES' / 'messages.po'

class TemplateConverter:
    def __init__(self):
        self.translations = defaultdict(dict)  # {nl_string: en_string, ...}
        self.processed_files = []
        self.converted_count = 0

    def extract_lang_blocks(self, content):
        """Extract {% if lang %} blocks from template."""
        # Pattern: {% if lang == 'en' %}english_text{% else %}dutch_text{% endif %}
        pattern = r"{%\s*if\s+lang\s*==\s*['\"]en['\"]\s*%}(.*?){%\s*else\s*%}(.*?){%\s*endif\s*%}"
        matches = re.finditer(pattern, content, re.DOTALL)
        return matches

    def escape_for_po(self, text):
        """Escape text for .po file format."""
        text = text.strip()
        # Escape newlines
        text = text.replace('\n', '\\n')
        # Escape quotes
        text = text.replace('"', '\\"')
        return text

    def convert_block(self, match):
        """Convert a single {% if lang %} block to {{ _() }} call."""
        en_text = match.group(1).strip()
        nl_text = match.group(2).strip()

        # Skip if text is too short or complex
        if not en_text or not nl_text:
            return match.group(0)

        # Store for .po file update
        self.translations[en_text] = nl_text
        self.converted_count += 1

        # Create the replacement - use English as key
        escaped_en = en_text.replace("'", "\\'").replace('"', '\\"')
        return f"{{{{ _({repr(en_text)}) }}}}"

    def convert_file(self, filepath):
        """Convert a single template file."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                original = f.read()

            # Find and replace all lang blocks
            converted = re.sub(
                r"{%\s*if\s+lang\s*==\s*['\"]en['\"]\s*%}(.*?){%\s*else\s*%}(.*?){%\s*endif\s*%}",
                self._replace_block,
                original,
                flags=re.DOTALL
            )

            # Only write if changes were made
            if converted != original:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(converted)
                self.processed_files.append((filepath, True))
                return True
            else:
                self.processed_files.append((filepath, False))
                return False

        except Exception as e:
            print(f"❌ Error processing {filepath}: {e}")
            return False

    def _replace_block(self, match):
        """Replacement function for regex substitution."""
        en_text = match.group(1).strip()
        nl_text = match.group(2).strip()

        if not en_text or not nl_text:
            return match.group(0)

        # Store translations
        self.translations[en_text] = nl_text
        self.converted_count += 1

        # Return converted text
        return f"{{{{ _({repr(en_text)}) }}}}"

    def update_po_files(self):
        """Add extracted strings to .po files."""
        if not self.translations:
            return

        # Update English .po file
        self._update_po_file(EN_PO_FILE, {k: k for k in self.translations.keys()})

        # Update Dutch .po file
        self._update_po_file(NL_PO_FILE, self.translations)

    def _update_po_file(self, po_file, translations_dict):
        """Update a single .po file with new translations."""
        if not po_file.exists():
            print(f"⚠️  {po_file} not found, skipping")
            return

        try:
            with open(po_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Split into header and entries
            parts = content.split('\n\n', 1)
            if len(parts) < 2:
                header = content
                entries = ""
            else:
                header, entries = parts

            # Add new entries at the end
            new_entries = []
            for msgid, msgstr in translations_dict.items():
                # Skip if already exists
                if f'msgid "{msgid}"' in content:
                    continue

                new_entry = f'''
msgid "{self._escape_for_po(msgid)}"
msgstr "{self._escape_for_po(msgstr)}"
'''
                new_entries.append(new_entry)

            if new_entries:
                updated_content = header + '\n\n' + entries + '\n'.join(new_entries)
                with open(po_file, 'w', encoding='utf-8') as f:
                    f.write(updated_content)
                print(f"📝 Added {len(new_entries)} new entries to {po_file.name}")

        except Exception as e:
            print(f"❌ Error updating {po_file}: {e}")

    def _escape_for_po(self, text):
        """Escape text for .po file."""
        text = text.replace('\\', '\\\\')
        text = text.replace('"', '\\"')
        text = text.replace('\n', '\\n')
        return text

    def run(self):
        """Main execution."""
        print("🔄 Converting templates to Flask-Babel format...\n")

        # Find all HTML templates
        template_files = list(TEMPLATES_DIR.rglob('*.html'))
        print(f"Found {len(template_files)} templates\n")

        # Process each template
        converted = 0
        for template_file in sorted(template_files):
            if self.convert_file(template_file):
                rel_path = template_file.relative_to(TEMPLATES_DIR)
                print(f"✅ {rel_path}")
                converted += 1

        print(f"\n📊 Summary:")
        print(f"  Files processed: {len(self.processed_files)}")
        print(f"  Files modified: {converted}")
        print(f"  Total conversions: {self.converted_count}")

        # Update .po files
        if self.translations:
            print(f"\n📝 Updating .po files with {len(self.translations)} new strings...")
            self.update_po_files()

        print("\n✅ Conversion complete!")
        return 0 if converted > 0 else 1


if __name__ == '__main__':
    converter = TemplateConverter()
    sys.exit(converter.run())
