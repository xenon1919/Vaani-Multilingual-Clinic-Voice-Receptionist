from tools.masking import mask_dict, mask_name, mask_phone, mask_transcript


def test_mask_phone():
    masked = mask_phone("9876543210")
    assert masked.startswith("98")
    assert masked.endswith("10")
    assert "9876543210" not in masked


def test_mask_name():
    masked = mask_name("Ramesh Kumar")
    assert masked.startswith("R")
    assert "Ramesh" not in masked
    assert "Kumar" not in masked


def test_mask_dict_only_masks_known_pii_keys():
    masked = mask_dict({"patient_name": "Ramesh Kumar", "phone_number": "9876543210", "date": "2026-10-16"})
    assert masked["date"] == "2026-10-16"
    assert "Ramesh" not in masked["patient_name"]
    assert "9876543210" not in masked["phone_number"]


def test_mask_dict_recurses_into_nested_dicts():
    masked = mask_dict({"appointment": {"patient_name": "Ramesh Kumar", "doctor_name": "Dr. Rao"}})
    assert "Ramesh" not in masked["appointment"]["patient_name"]
    assert masked["appointment"]["doctor_name"] == "Dr. Rao"


def test_mask_dict_scrubs_name_from_free_text_message():
    masked = mask_dict({
        "patient_name": "Anita Test Patient",
        "message": "Booked Anita Test Patient with Dr. Rao on 2026-10-16 at 09:00.",
    })
    assert "Anita" not in masked["message"]
    assert "Dr. Rao" in masked["message"]


def test_mask_dict_recurses_into_lists_of_dicts():
    masked = mask_dict({
        "options": [
            {"patient_name": "Ramesh Kumar", "doctor_name": "Dr. Rao"},
            {"patient_name": "Sita Devi", "doctor_name": "Dr. Mehta"},
        ]
    })
    assert "Ramesh" not in masked["options"][0]["patient_name"]
    assert "Sita" not in masked["options"][1]["patient_name"]


def test_mask_transcript_masks_bare_phone_numbers():
    masked = mask_transcript("My number is 9876543210, please call me.")
    assert "9876543210" not in masked
