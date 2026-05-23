"""
schema.py — 知识抽取 Pydantic 模型定义
"""

from typing import Optional, List, Literal  # noqa: F401
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    section: str = ""
    original_text: str = ""


class Concept(BaseModel):
    concept_id: str
    term: str
    normalized: str = ""
    std_label: str = ""
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class Relation(BaseModel):
    relation_id: str
    head: str = ""
    tail: str = ""
    head_term: str = ""
    tail_term: str = ""
    relation_type: str = ""
    relation_surface: str = ""
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class Dataset(BaseModel):
    dataset_id: str
    name: str
    modality: str = ""
    domain: str = ""
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class Method(BaseModel):
    method_id: str
    name: str
    method_type: str = ""
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class Experiment(BaseModel):
    experiment_id: str
    task: str = ""
    setup: str = ""
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class QuantitativeResult(BaseModel):
    qr_id: str
    quantity: str = ""
    value: Optional[float] = None
    unit: str = ""
    context: str = ""
    result_type: str = ""
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class PerformanceResult(BaseModel):
    perf_id: str
    metric: str = ""
    compared_to: str = ""
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class DataSpecification(BaseModel):
    ds_id: str
    spec_type: str = ""
    description: str = ""
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class Conclusion(BaseModel):
    conclusion_id: str
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class Claim(BaseModel):
    claim_id: str
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class FutureWork(BaseModel):
    future_work_id: str
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


class Limitation(BaseModel):
    limitation_id: str
    evidence: Evidence = Evidence(section="", original_text="")
    confidence: float = 1.0


Entry = Concept | Relation | Dataset | Method | Experiment | PerformanceResult | QuantitativeResult | DataSpecification | Conclusion | Claim | FutureWork | Limitation


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
    entries: list = []


class LLMExtractionResponse(BaseModel):
    entries: list = []


class LLMDisciplineResponse(BaseModel):
    title: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    primary_discipline: dict = Field(default_factory=dict)
    secondary_disciplines: Optional[List[dict]] = None
    keywords: List[str] = Field(default_factory=list)
