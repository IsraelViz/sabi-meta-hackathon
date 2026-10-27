"""Per-message state shared by the orchestrator and the tools."""
from dataclasses import dataclass, field

from app.normalize.amounts import Normalized


@dataclass
class Outgoing:
    to: str
    body: str
    buttons: list[dict] = field(default_factory=list)   # [{"id": str, "title": str}], max 3
    image_url: str | None = None


@dataclass
class RunContext:
    store: object
    persona: str                          # 'owner' | 'customer'
    sender: str                           # phone number of whoever wrote
    owner_phone: str
    auto_post_limit_kobo: int
    evidence_id: str | None = None
    message: Normalized | None = None
    contact_id: str | None = None         # customer mode: the customer's own contact record
    image_data_url: str | None = None     # photo attached to this message, if any
    llm: object = None
    document: dict | None = None          # what read_document extracted from the photo
    document_issues: list = field(default_factory=list)   # arithmetic problems; recording is blocked until settled
    counter: object = None                # stock-take counter (SAM or vision), injectable for tests
    outbox: list[Outgoing] = field(default_factory=list)
    match_scores: dict = field(default_factory=dict)   # id -> best fuzzy score seen this run
    committed: list = field(default_factory=list)
    tool_log: list = field(default_factory=list)

    def say(self, body, buttons=None, to=None, image_url=None):
        self.outbox.append(Outgoing(to or self.sender, body, buttons or [], image_url))

    def tell_owner(self, body, buttons=None):
        self.say(body, buttons, to=self.owner_phone)
