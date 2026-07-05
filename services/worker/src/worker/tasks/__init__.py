"""Worker tasks.

Importing every task module here registers it with Celery: the app uses
``autodiscover_tasks(["worker.tasks"])``, which imports this package, so any
task NOT re-exported here is never registered and its messages are discarded
as "unregistered task".
"""

from worker.tasks.extract import task_extract_and_ingest
from worker.tasks.ingest import task_ingest_document
from worker.tasks.metadata import task_update_document_metadata

__all__ = [
    "task_extract_and_ingest",
    "task_ingest_document",
    "task_update_document_metadata",
]
