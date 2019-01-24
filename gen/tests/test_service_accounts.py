import uuid

import gen
import pkgpanda.util
from gen.tests.utils import make_arguments, true_false_msg, validate_error


class TestServiceAccounts:
    """
    Tests for superuser_service_account_uid/public_key.

    """

    def test_superuser_service_account_public_key_invalid(self):
        """
        An error is shown when ``superuser_service_account_public_key`` is
        given a value which is not an RSA public key encoded in the OpenSSL PEM
        format.
        """
        validate_error(
            new_arguments={
                'superuser_service_account_uid': str(uuid.uuid4()),
                'superuser_service_account_public_key': str(uuid.uuid4()),
            },
            key='_superuser_service_account_public_key_json',
            message=(
               'superuser_service_account_public_key has an invalid value. It '
               'must hold an RSA public key encoded in the OpenSSL PEM '
               'format. Error: Could not deserialize key data.'
            )
        )

    def test_superuser_service_account_uid_not_specified(self):
        """
        An error is shown when ``superuser_service_account_public_key`` is
        specified without a corresponding ``superuser_service_account_uid``.
        """
        validate_error(
            new_arguments={'superuser_service_account_uid': str(uuid.uuid4())},
            key='_superuser_credentials_given',
            message=(
                "'superuser_service_account_uid' and "
                "'superuser_service_account_public_key' "
                "must both be empty or both be non-empty"
            )
        )

    def test_superuser_service_account_public_key_not_specified(self):
        """
        An error is shown when ``superuser_service_account_uid`` is specified
        without a corresponding ``superuser_service_account_public_key``.
        """
        validate_error(
            new_arguments={'superuser_service_account_uid': str(uuid.uuid4())},
            key='_superuser_credentials_given',
            message=(
                "'superuser_service_account_uid' and "
                "'superuser_service_account_public_key' "
                "must both be empty or both be non-empty"
            )
        )
