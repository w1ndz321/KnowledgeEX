"""
schema.py — 知识抽取 Pydantic 模型定义
"""

from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    section: str = ""
    page: Optional[int] = None
    anchor_text: str = ""
    original_text: str = ""
    match_method: Literal["exact", "unique_fragment", "unmatched"] = "unmatched"


class ConceptPayload(BaseModel):
    term: str
    normalized: str = ""
    std_label: str = ""


class RelationPayload(BaseModel):
    head_entry_id: Optional[str] = None
    tail_entry_id: Optional[str] = None
    head_term: str = ""
    tail_term: str = ""
    relation_type: str = ""
    relation_surface: str = ""


class DatasetPayload(BaseModel):
    name: str
    modality: str = ""
    domain: str = ""


class MethodPayload(BaseModel):
    name: str
    method_type: str = ""


class ExperimentPayload(BaseModel):
    task: str = ""
    setup: str = ""


class QuantitativeResultPayload(BaseModel):
    quantity: str = ""
    value: Optional[float | str] = None
    unit: str = ""
    context: str = ""
    result_type: str = ""


class PerformanceResultPayload(BaseModel):
    metric: str = ""
    compared_to: str = ""


class DataSpecificationPayload(BaseModel):
    spec_type: str = ""
    description: str = ""


class EmptyPayload(BaseModel):
    pass


PAYLOAD_MODELS = {
    "concept": ConceptPayload,
    "relation": RelationPayload,
    "dataset": DatasetPayload,
    "method": MethodPayload,
    "experiment": ExperimentPayload,
    "performance_result": PerformanceResultPayload,
    "quantitative_result": QuantitativeResultPayload,
    "data_specification": DataSpecificationPayload,
    "conclusion": EmptyPayload,
    "claim": EmptyPayload,
    "future_work": EmptyPayload,
    "limitation": EmptyPayload,
}


class Entry(BaseModel):
    entry_id: str
    type: str
    payload: dict = Field(default_factory=dict)
    evidence: Evidence = Field(default_factory=Evidence)
    confidence: float = 1.0


class DisciplineLevel(BaseModel):
    level1: Optional[str] = None
    level2: Optional[str] = None
    level3: Optional[str] = None


class ExtractionInfo(BaseModel):
    extraction_model: str = ""
    extraction_timestamp: str = ""
    extraction_method: str = ""
    retry_groups: Optional[list[str]] = None
    failed_groups: Optional[list[str]] = None


class Metadata(BaseModel):
    doc_id: str
    source_pdf_sha256_96: str = ""
    converted_text_sha256_96: str = ""
    source_file: str = ""
    title: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: str = ""
    introduction: str = ""
    primary_discipline: DisciplineLevel = Field(default_factory=DisciplineLevel)
    secondary_disciplines: Optional[List[DisciplineLevel]] = None
    keywords: List[str] = Field(default_factory=list)
    extraction_info: Optional[ExtractionInfo] = None


class ExtractionOutput(BaseModel):
    metadata: Metadata
    entries: list[Entry] = Field(default_factory=list)


class LLMExtractionResponse(BaseModel):
    entries: list = Field(default_factory=list)


class LLMDisciplineResponse(BaseModel):
    title: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    primary_discipline: dict = Field(default_factory=dict)
    secondary_disciplines: Optional[List[dict]] = None
    keywords: List[str] = Field(default_factory=list)
