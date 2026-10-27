from app.auth.password import hash_password, verify_password


def test_password_is_stored_as_an_argon2_hash() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert password_hash != "correct horse battery staple"
    assert password_hash.startswith("$argon2")
    assert verify_password("correct horse battery staple", password_hash)
    assert not verify_password("incorrect", password_hash)


def test_blank_passwords_are_rejected_or_fail_verification() -> None:
    try:
        hash_password("")
    except ValueError as exc:
        assert str(exc) == "password cannot be blank"
    else:
        raise AssertionError("blank password must be rejected")

    assert not verify_password("", "not-a-hash")
