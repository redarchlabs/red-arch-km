"""Worker tasks.

Importing every task module here registers it with Celery: the app uses
``autodiscover_tasks(["worker.tasks"])``, which imports this package, so any
task NOT re-exported here is never registered and its messages are discarded
as "unregistered task".
"""

from worker.tasks.extract import task_extract_and_ingest
from worker.tasks.ingest import task_ingest_document
from worker.tasks.metadata import task_update_document_metadata
from worker.tasks.monitoring import beat_heartbeat
from worker.tasks.workflow import maintain_partitions, sweep_outbox

__all__ = [
    "task_extract_and_ingest",
    "task_ingest_document",
    "task_update_document_metadata",
    # Workflow-engine + monitoring tasks fired by celery-beat. These MUST be
    # re-exported here or autodiscover never registers them and beat's messages
    # are dropped as "unregistered task" (the outbox then never gets swept).
    "beat_heartbeat",
    "maintain_partitions",
    "sweep_outbox",
]
