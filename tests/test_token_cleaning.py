import unittest

from engine.credentials import clean_token

# Montado em partes: o literal inteiro tem o formato de token de bot e trava o push no GitHub.
TOKEN = ".".join(("MTIzNDU2Nzg5MDEyMzQ1Njc4", "GabcDE", "abcdefghijklmnopqrstuvwxyz0123456789AB"))

# Caracteres invisíveis escritos por código para não sumirem no editor.
BOM, ZWSP, ZWNJ, ZWJ, WORD_JOINER, NBSP = (chr(c) for c in (0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060, 0x00A0))
CURLY = ((chr(0x201C), chr(0x201D)), (chr(0x2018), chr(0x2019)))


class CleanTokenTests(unittest.TestCase):
    def test_plain_token_is_unchanged(self):
        self.assertEqual(clean_token(TOKEN), TOKEN)

    def test_strips_surrounding_quotes_of_every_kind(self):
        for left, right in (('"', '"'), ("'", "'"), ("`", "`"), *CURLY):
            with self.subTest(quote=left):
                self.assertEqual(clean_token(f"{left}{TOKEN}{right}"), TOKEN)

    def test_strips_nested_quotes_and_spaces(self):
        self.assertEqual(clean_token(f"  \"'{TOKEN}'\"  "), TOKEN)

    def test_removes_whitespace_and_line_breaks_anywhere(self):
        self.assertEqual(clean_token(f"{TOKEN[:20]} \n{TOKEN[20:40]}\t\r\n{TOKEN[40:]}"), TOKEN)

    def test_removes_invisible_characters(self):
        dirty = BOM + TOKEN[:10] + ZWSP + TOKEN[10:30] + ZWNJ + ZWJ + WORD_JOINER + TOKEN[30:] + NBSP
        self.assertEqual(clean_token(dirty), TOKEN)

    def test_removes_authorization_prefix(self):
        self.assertEqual(clean_token(f"Authorization: {TOKEN}"), TOKEN)
        self.assertEqual(clean_token(f'authorization:"{TOKEN}"'), TOKEN)

    def test_removes_authorization_prefix_inside_quotes(self):
        self.assertEqual(clean_token(f'  "{ZWSP}Authorization: {TOKEN}\n"  '), TOKEN)
        self.assertEqual(clean_token(f"'Authorization:\"{TOKEN}\"'"), TOKEN)

    def test_keeps_inner_characters_that_belong_to_the_token(self):
        self.assertEqual(clean_token("abc.def-ghi_jkl"), "abc.def-ghi_jkl")

    def test_empty_and_none(self):
        self.assertEqual(clean_token(""), "")
        self.assertEqual(clean_token(None), "")
        self.assertEqual(clean_token('  " "  '), "")


if __name__ == "__main__":
    unittest.main()
