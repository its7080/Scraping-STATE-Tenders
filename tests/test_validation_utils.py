import unittest

from Program_Files.validation_utils import is_valid_portal_name, is_valid_portal_url


class PortalValidationTests(unittest.TestCase):
    def test_valid_portal_urls(self):
        self.assertTrue(is_valid_portal_url("https://eprocure.gov.in"))
        self.assertTrue(is_valid_portal_url("http://example.org/path?q=1"))

    def test_invalid_portal_urls(self):
        self.assertFalse(is_valid_portal_url(""))
        self.assertFalse(is_valid_portal_url("ftp://example.org"))
        self.assertFalse(is_valid_portal_url("not-a-url"))
        self.assertFalse(is_valid_portal_url("https:///missing-host"))

    def test_valid_portal_names(self):
        self.assertTrue(is_valid_portal_name("West Bengal eProc"))
        self.assertTrue(is_valid_portal_name("IREPS"))

    def test_invalid_portal_names(self):
        self.assertFalse(is_valid_portal_name(""))
        self.assertFalse(is_valid_portal_name("   "))
        self.assertFalse(is_valid_portal_name("Name:WithColon"))
        self.assertFalse(is_valid_portal_name("Name\nNewline"))


if __name__ == "__main__":
    unittest.main()
