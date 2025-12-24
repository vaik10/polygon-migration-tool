"""Storage backend interface for test-case storage managers.

Defines the abstract `StorageManager` that concrete backends (Azure, local FS,
S3, etc.) should implement. Keeping this in a separate module makes it easy
to swap implementations and to type-hint dependencies.
"""
from abc import ABC, abstractmethod


class StorageManager(ABC):
    """Abstract storage manager interface for test-case storage backends.

    Concrete storage managers should implement `upload_test_case` and
    `empty_blob` so the application can swap implementations (Azure, local
    FS, S3, etc.).
    """

    @abstractmethod
    def upload_test_case(self, container_name, db_problem_id, test_number, input_data, output_data):
        """Upload a single test case (input + output) to the storage backend.

        Args:
            container_name (str): Container or bucket name.
            db_problem_id (str|int): Database problem id used for namespacing.
            test_number (int): 1-based test case index.
            input_data (str): Test input contents.
            output_data (str): Test output contents.
        """
        raise NotImplementedError()

    @abstractmethod
    def empty_blob(self, container_name, problem_id):
        """Remove all blobs/files associated with `problem_id` from storage.

        Args:
            container_name (str): Container or bucket name.
            problem_id (str|int): Problem id used for namespacing.
        """
        raise NotImplementedError()
