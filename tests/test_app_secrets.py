import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from streamlit.testing.v1 import AppTest

from minimalist_mvp.product import manual_product_extraction
from tests.test_generation import FakeClient, valid_draft


class ServerSideSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        image = BytesIO()
        Image.new("RGB", (300, 600), "#ffffff").save(image, "PNG")
        product = manual_product_extraction(
            source_url="https://beminimalist.co/products/test",
            product_name="Test Serum",
            variant="",
            facts_text="2% active helps reduce excess oil",
            claims_text="",
            evidence_text="",
            commercial_text="",
            social_proof_text="",
            image_name="manual.png",
        )
        self.app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
        )
        self.app.secrets["OPENAI_API_KEY"] = ""
        self.app.session_state.product_extraction = product
        self.app.session_state.manual_product_image = image.getvalue()
        self.app.run(timeout=20)
        self.assertFalse(self.app.exception)
        next(
            widget
            for widget in self.app.checkbox
            if widget.label.startswith("I have checked the product identity")
        ).check().run(timeout=20)
        self.assertFalse(self.app.exception)

    def test_missing_key_has_no_frontend_input_or_generation(self) -> None:
        self.assertTrue(
            any(
                error.value == "OpenAI API key is not configured on the server."
                for error in self.app.error
            )
        )
        self.assertFalse(
            any(widget.label == "OpenAI API key" for widget in self.app.text_input)
        )
        button = next(
            widget for widget in self.app.button if widget.label == "Generate one creative"
        )
        self.assertTrue(button.disabled)

    def test_configured_server_secret_enables_generation_without_exposing_key(self) -> None:
        placeholder_key = "unit-test-server-key"
        self.app.secrets["OPENAI_API_KEY"] = placeholder_key
        with patch("openai.OpenAI", return_value=FakeClient(valid_draft())) as client:
            self.app.run(timeout=20)
            self.assertFalse(self.app.exception)
            button = next(
                widget for widget in self.app.button if widget.label == "Generate one creative"
            )
            self.assertFalse(button.disabled)
            button.click().run(timeout=20)

        self.assertFalse(self.app.exception)
        client.assert_called_once_with(api_key=placeholder_key)
        self.assertEqual(
            self.app.session_state.generated_creative["draft"]["headline"]["text"],
            "Test Serum",
        )
        self.assertTrue(self.app.session_state.generated_preview)
        self.assertNotIn(placeholder_key, str(self.app.session_state.generated_creative))
        self.assertNotIn(placeholder_key.encode(), self.app.session_state.generated_preview)
        self.assertFalse(
            any(widget.label == "OpenAI API key" for widget in self.app.text_input)
        )


if __name__ == "__main__":
    unittest.main()
