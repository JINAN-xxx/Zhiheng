import io
import unittest

from PIL import Image

from platform_shell_check import compare_screenshots


def screenshot(size=(8, 8), changed_pixels=0, color=(108, 92, 108)):
    image = Image.new("RGB", size, (100, 100, 100))
    for index in range(changed_pixels):
        image.putpixel((index % size[0], index // size[0]), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ScreenshotComparisonTests(unittest.TestCase):
    def test_identical_images_pass(self):
        original = screenshot()
        self.assertEqual(compare_screenshots(original, original), (0, 0))

    def test_small_antialiasing_difference_at_both_limits_passes(self):
        self.assertEqual(
            compare_screenshots(screenshot(), screenshot(changed_pixels=32)),
            (32, 8),
        )

    def test_one_pixel_with_large_color_change_fails(self):
        with self.assertRaises(AssertionError):
            compare_screenshots(screenshot(), screenshot(changed_pixels=1, color=(109, 100, 100)))

    def test_widespread_small_color_changes_fail(self):
        with self.assertRaises(AssertionError):
            compare_screenshots(screenshot(), screenshot(changed_pixels=33, color=(101, 100, 100)))

    def test_changed_dimensions_fail(self):
        with self.assertRaises(AssertionError):
            compare_screenshots(screenshot(), screenshot(size=(8, 9)))


if __name__ == "__main__":
    unittest.main()
