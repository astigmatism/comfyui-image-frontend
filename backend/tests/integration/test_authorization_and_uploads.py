from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient
from tests.conftest import change_password, create_user, csrf, login
from tests.fake_services import make_png
from tests.helpers import (
    ADMIN_PASSWORD,
    ADMIN_TEMP,
    USER_PASSWORD,
    USER_TEMP,
    create_generation,
    generation_payload,
    restore_cookie,
    wait_for_status,
)


def _prepare_accounts(client: TestClient) -> tuple[dict, str, dict, str, str]:
    login(client, "admin", ADMIN_TEMP)
    change_password(client, ADMIN_PASSWORD)
    alice = create_user(client, "alice.user", USER_TEMP)
    bob = create_user(client, "bob.user", "BobTemporary123!")
    admin_cookie = client.cookies.get("cif_session")
    assert admin_cookie

    client.cookies.clear()
    login(client, "alice.user", USER_TEMP)
    change_password(client, USER_PASSWORD)
    alice_cookie = client.cookies.get("cif_session")
    assert alice_cookie

    client.cookies.clear()
    login(client, "bob.user", "BobTemporary123!")
    change_password(client, "BobPermanent123!")
    bob_cookie = client.cookies.get("cif_session")
    assert bob_cookie
    return alice, alice_cookie, bob, bob_cookie, admin_cookie


def test_cross_user_and_administrator_content_access_is_denied(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        _, alice_cookie, _, bob_cookie, admin_cookie = _prepare_accounts(client)
        restore_cookie(client, alice_cookie)
        upload_response = client.post(
            "/api/uploads/images",
            headers={"X-CSRF-Token": csrf(client)},
            files={"file": ("source.png", make_png("source"), "image/png")},
        )
        assert upload_response.status_code == 200, upload_response.text
        upload = upload_response.json()
        generation = create_generation(
            client,
            "private alice image",
            seed=77,
            source_upload_id=upload["id"],
        )
        detail = wait_for_status(client, generation["id"], "succeeded")
        artifact = detail["artifacts"][-1]

        for cookie in (bob_cookie, admin_cookie):
            restore_cookie(client, cookie)
            assert client.get(f"/api/generations/{generation['id']}").status_code == 404
            assert client.get(f"/api/generations/{generation['id']}/recall").status_code == 404
            assert client.get(artifact["content_url"]).status_code == 404
            assert client.get(artifact["thumbnail_url"]).status_code == 404
            assert client.get(upload["preview_url"]).status_code == 404
            assert (
                client.post(
                    f"/api/uploads/reference-images/from-artifact/{artifact['id']}",
                    headers={"X-CSRF-Token": csrf(client)},
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/generations/{generation['id']}/cancel",
                    headers={"X-CSRF-Token": csrf(client)},
                ).status_code
                == 404
            )
            assert (
                client.delete(
                    f"/api/generations/{generation['id']}",
                    headers={"X-CSRF-Token": csrf(client)},
                ).status_code
                == 404
            )

        restore_cookie(client, bob_cookie)
        foreign_upload_request = generation_payload(
            client, "attempt foreign upload", seed=78, source_upload_id=upload["id"]
        )
        # Publication v1 accepts only declared scalar parameters. An upload identifier cannot
        # be smuggled into this source, regardless of ownership.
        foreign_upload_request["parameters"]["source_upload_id"] = upload["id"]
        response = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=foreign_upload_request,
        )
        assert response.status_code == 422
        assert response.json()["error"]["fields"] == {
            "source_upload_id": "Unknown published parameter."
        }
        assert response.json()["error"]["code"] == "parameter_validation_failed"

        restore_cookie(client, alice_cookie)
        assert client.get(f"/api/generations/{generation['id']}").status_code == 200
        assert client.get(artifact["content_url"]).status_code == 200


def test_upload_validation_and_path_traversal_protection(app_client: TestClient) -> None:
    # Prepare one normal user in the shared app fixture.
    login(app_client, "admin", ADMIN_TEMP)
    change_password(app_client, ADMIN_PASSWORD)
    create_user(app_client, "upload.user", USER_TEMP)
    app_client.cookies.clear()
    login(app_client, "upload.user", USER_TEMP)
    change_password(app_client, USER_PASSWORD)

    text = app_client.post(
        "/api/uploads/images",
        headers={"X-CSRF-Token": csrf(app_client)},
        files={"file": ("not-an-image.txt", b"not an image", "text/plain")},
    )
    assert text.status_code == 415

    disguised = app_client.post(
        "/api/uploads/images",
        headers={"X-CSRF-Token": csrf(app_client)},
        files={"file": ("fake.png", b"not an image", "image/png")},
    )
    assert disguised.status_code == 400
    assert disguised.json()["error"]["code"] == "upload_invalid"

    assert app_client.get("/api/uploads/../../etc/passwd/content").status_code in {404, 422}
    assert app_client.get("/api/artifacts/../../etc/passwd/content").status_code in {404, 422}
