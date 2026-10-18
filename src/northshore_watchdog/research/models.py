from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    OFFICIAL_GOV = "OFFICIAL_GOV"
    BC_ASSESSMENT = "BC_ASSESSMENT"
    MLS = "MLS"
    VOW = "VOW"
    AGGREGATOR = "AGGREGATOR"
    MANUAL = "MANUAL"


class SourceRole(str, Enum):
    """Operational role used to order and explain acquisition sources."""

    MLS_PRIMARY = "MLS_PRIMARY"
    REALTOR_CA_PUBLIC = "REALTOR_CA_PUBLIC"
    ZOLO_PUBLIC = "ZOLO_PUBLIC"
    OFFICIAL_ENRICHMENT = "OFFICIAL_ENRICHMENT"
    LEGACY_FIXTURE = "LEGACY_FIXTURE"
    UNSPECIFIED = "UNSPECIFIED"


class OwnershipType(str, Enum):
    FREEHOLD_NON_STRATA = "FREEHOLD_NON_STRATA"
    FREEHOLD_STRATA = "FREEHOLD_STRATA"
    LEASEHOLD = "LEASEHOLD"
    PREPAID_LEASE = "PREPAID_LEASE"
    NON_PREPAID_LEASE = "NON_PREPAID_LEASE"
    BARE_LAND_STRATA = "BARE_LAND_STRATA"
    FRACTIONAL_OWNERSHIP = "FRACTIONAL_OWNERSHIP"
    UNDIVIDED_INTEREST = "UNDIVIDED_INTEREST"
    PARTIAL_INTEREST = "PARTIAL_INTEREST"
    COURT_SALE = "COURT_SALE"
    FORECLOSURE_COURT_ORDERED_SALE = "FORECLOSURE_COURT_ORDERED_SALE"
    COOPERATIVE_SHARE = "COOPERATIVE_SHARE"
    UNKNOWN = "UNKNOWN"


class ApprovedUse(str, Enum):
    SINGLE_DETACHED = "SINGLE_DETACHED"
    SINGLE_DETACHED_SECONDARY_SUITE = "SINGLE_DETACHED_SECONDARY_SUITE"
    TWO_FAMILY_DWELLING = "TWO_FAMILY_DWELLING"
    MULTIPLE_DWELLING = "MULTIPLE_DWELLING"
    LANEWAY_HOUSE = "LANEWAY_HOUSE"
    STRATA = "STRATA"
    NON_CONFORMING_USE = "NON_CONFORMING_USE"
    UNKNOWN = "UNKNOWN"


class CandidateStatus(str, Enum):
    A_STRONG_CANDIDATE = "A_STRONG_CANDIDATE"
    B_WATCH = "B_WATCH"
    C_OPPORTUNISTIC = "C_OPPORTUNISTIC"
    D_REJECT = "D_REJECT"
    NEED_VERIFICATION = "NEED_VERIFICATION"
    REJECT = "REJECT"
    SPECIAL_SITUATION = "SPECIAL_SITUATION"


class CaseQuality(str, Enum):
    TRACEABLE_RESEARCH = "TRACEABLE_RESEARCH"
    BOUNDARY_FIXTURE = "BOUNDARY_FIXTURE"
    COVERAGE_GAP = "COVERAGE_GAP"


class LotShape(str, Enum):
    REGULAR_RECTANGULAR = "regular_rectangular"
    CORNER_REGULAR = "corner_regular"
    IRREGULAR = "irregular"
    PIE = "pie"
    FLAG = "flag"
    STEEP = "steep"
    OTHER = "other"


class RoadClass(str, Enum):
    HIGHWAY_FREEWAY_RAIL = "A"
    MAJOR_ARTERIAL_TRUCK = "B"
    SECONDARY_ARTERIAL = "C"
    COLLECTOR = "D"
    QUIET_RESIDENTIAL = "E"


class ScenarioType(str, Enum):
    KEEP_EXISTING_HOUSE = "KEEP_EXISTING_HOUSE"
    DETACHED_REBUILD = "DETACHED_REBUILD"
    DUPLEX = "DUPLEX"
    MAX_REALISTIC_MULTIPLEX = "MAX_REALISTIC_MULTIPLEX"
    CHARACTER_HOUSE_INFILL = "CHARACTER_HOUSE_INFILL"
    TOA_OR_ASSEMBLY = "TOA_OR_ASSEMBLY"


class EvidenceStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    UNCONFIRMED = "UNCONFIRMED"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class OfficialQueryStatus(str, Enum):
    SUCCESS = "SUCCESS"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"


class _ResearchModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class PropertyRecord(_ResearchModel):
    property_id: str
    address: str
    municipality: str
    neighbourhood: str | None = None
    property_type: str | None = None
    # Research classification is deliberately separate from legal ownership
    # and approved use.  A listing can say "detached" while still requiring
    # title/municipal confirmation, so this is a scope label, not entitlement.
    research_asset_class: str = "UNKNOWN"
    research_asset_classification_basis: str | None = None
    research_asset_classification_notes: list[str] = Field(default_factory=list)
    primary_detached_eligible: bool | None = None
    research_scope: str = "UNKNOWN"
    research_scope_reasons: list[str] = Field(default_factory=list)
    market_bucket: str | None = None
    range_classification: str = "UNKNOWN"

    secondary_civic_addresses: list[str] = Field(default_factory=list)
    pid: str | None = Field(default=None, alias="PID")
    pids: list[str] = Field(default_factory=list)
    legal_description: str | None = None
    number_of_pids: int | None = None
    strata_lot_identifiers: list[str] = Field(default_factory=list)
    ownership_type: OwnershipType = OwnershipType.UNKNOWN
    # Listing feeds may publish a tenure/title claim, but it is retained as
    # unverified provenance until an official source confirms legal ownership.
    unverified_ownership_claim: str | None = None
    approved_use: ApprovedUse = ApprovedUse.UNKNOWN
    building_permits: list[dict[str, Any]] = Field(default_factory=list)
    occupancy_permits: list[dict[str, Any]] = Field(default_factory=list)
    secondary_suite_permits: list[dict[str, Any]] = Field(default_factory=list)
    two_family_dwelling_status: str | None = None
    outstanding_permits: list[dict[str, Any]] = Field(default_factory=list)
    bylaw_violations: list[dict[str, Any]] = Field(default_factory=list)
    single_title_multi_address: bool | None = None
    independently_tradable: bool | None = None
    title_status: str | None = None
    serious_litigation: bool | None = None
    serious_unresolved_strata_issue: bool | None = None
    major_data_conflict: bool | None = None
    vacant_possession_possible: bool | None = None
    redevelopment_legal_blocked: bool | None = None
    strata_fee: float | None = Field(default=None, ge=0)
    strata_form_b_status: str | None = None
    strata_bylaws_status: str | None = None
    strata_insurance_status: str | None = None
    strata_crf: float | None = Field(default=None, ge=0)
    common_property: str | None = None
    limited_common_property: str | None = None
    unit_entitlement: str | None = None
    repair_responsibility: str | None = None

    listing_id: str | None = None
    mls_number: str | None = None
    source_url: str | None = None
    source_capture_note: str | None = None
    description: str | None = None
    listing_status: str | None = None
    acquisition_channel: str = "UNKNOWN"
    feed_class: str = "UNKNOWN"
    asking: int | None = Field(default=None, ge=0)
    original_asking: int | None = Field(default=None, ge=0)
    listing_date: datetime | None = None
    dom: int | None = Field(default=None, ge=0)
    cumulative_dom: int | None = Field(default=None, ge=0)
    relist_count: int = Field(default=0, ge=0)
    terminated_history: list[dict[str, Any]] = Field(default_factory=list)
    previous_listing_prices: list[int] = Field(default_factory=list)
    previous_sale_dates: list[datetime] = Field(default_factory=list)
    previous_sale_prices: list[int] = Field(default_factory=list)
    assessment_history: list[dict[str, Any]] = Field(default_factory=list)
    transaction_price: int | None = Field(default=None, ge=0)
    # A current listing can be observed as sold before a public sold price is
    # available. Keep the event date separate from the price and from BCA's
    # historical registered-sale window.
    transaction_date: datetime | None = None
    price_reduction_amount: int | None = None
    price_reduction_pct: float | None = None
    previous_listing_sources: list[str] = Field(default_factory=list)
    sold_history_sources: list[str] = Field(default_factory=list)
    transaction_history_conflict: bool = False
    realtor_price_sale_history: list[dict[str, Any]] = Field(default_factory=list)
    realtor_history_status: str | None = None
    school_district: str | None = None
    sold_history_scope: str | None = None
    official_enrichment_status: dict[str, str] = Field(default_factory=dict)

    assessment_year: int | None = None
    assessment_total: int | None = Field(default=None, ge=0)
    assessment_land: int | None = Field(default=None, ge=0)
    assessment_improvement: int | None = Field(default=None, ge=0)
    previous_year_total: int | None = Field(default=None, ge=0)
    previous_year_land: int | None = Field(default=None, ge=0)
    previous_year_improvement: int | None = Field(default=None, ge=0)
    land_ratio: float | None = None
    asking_vs_total_assessment: float | None = None
    asking_vs_land_assessment: float | None = None
    assessment_labels: list[str] = Field(default_factory=list)
    assessment_valuation_status: str = "UNKNOWN"

    lot_area_sqft: float | None = Field(default=None, ge=0)
    lot_area_m2: float | None = Field(default=None, ge=0)
    frontage_ft: float | None = Field(default=None, ge=0)
    depth_ft: float | None = Field(default=None, ge=0)
    lot_shape: LotShape | str | None = None
    lane_access: bool | None = None
    lane: bool | None = None
    corner_lot: bool | None = None
    corner: bool | None = None
    slope: str | float | None = None
    orientation: str | None = None
    rear_access: bool | None = None
    lot_size_percentile_in_neighbourhood: float | None = Field(default=None, ge=0, le=100)
    lot_area_score: float | None = Field(default=None, ge=0, le=100)
    frontage_score: float | None = Field(default=None, ge=0, le=100)
    depth_score: float | None = Field(default=None, ge=0, le=100)
    lot_geometry_score: float | None = Field(default=None, ge=0, le=100)

    year_built: int | None = None
    effective_year: int | None = None
    substantial_rebuild: bool | None = None
    finished_sqft: float | None = Field(default=None, ge=0)
    unfinished_sqft: float | None = Field(default=None, ge=0)
    bedrooms: float | None = Field(default=None, ge=0)
    bathrooms: float | None = Field(default=None, ge=0)
    basement: str | None = None
    basement_ceiling: str | None = None
    kitchens: int | None = Field(default=None, ge=0)
    separate_entrance: bool | None = None
    foundation: str | None = None
    roof: str | None = None
    heating: str | None = None
    electrical: str | None = None
    plumbing: str | None = None
    sewer: str | None = None
    drainage: str | None = None
    roof_age: float | None = Field(default=None, ge=0)
    knob_and_tube: str | bool | None = None
    galvanized_pipe: str | bool | None = None
    cast_iron: str | bool | None = None
    electrical_panel: str | None = None
    oil_tank: str | bool | None = None
    asbestos: str | bool | None = None
    moisture: str | bool | None = None
    perimeter_drain: str | bool | None = None
    current_house_utility_score: float | None = Field(default=None, ge=0, le=100)
    current_house_use_status: str = "UNKNOWN"
    current_house_use_reasons: list[str] = Field(default_factory=list)
    renovation_tier: str | None = None
    renovation_estimate: int | None = Field(default=None, ge=0)
    permanent_defects: list[str] = Field(default_factory=list)
    fixable_defects: list[str] = Field(default_factory=list)
    major_contamination: bool | None = None
    severe_foundation_problem: bool | None = None
    severe_uncompensated_permanent_defect: bool | None = None

    zoning: str | None = None
    bylaw_version: str | None = None
    official_zoning_confirmed: bool | None = None
    official_zoning_source_url: str | None = None
    official_zoning_source_registry_id: str | None = None
    official_zoning_evidence_status: EvidenceStatus = EvidenceStatus.UNKNOWN
    official_zoning_retrieved_at: datetime | None = None
    official_approved_use_confirmed: bool | None = None
    official_approved_use_source_url: str | None = None
    official_approved_use_source_registry_id: str | None = None
    official_approved_use_evidence_status: EvidenceStatus = EvidenceStatus.UNKNOWN
    official_approved_use_retrieved_at: datetime | None = None
    official_pid_confirmed: bool | None = None
    official_ownership_confirmed: bool | None = None
    official_title_source_url: str | None = None
    official_title_source_registry_id: str | None = None
    official_title_evidence_status: EvidenceStatus = EvidenceStatus.UNKNOWN
    official_title_retrieved_at: datetime | None = None
    permitted_uses: list[str] = Field(default_factory=list)
    conditional_uses: list[str] = Field(default_factory=list)
    minimum_site_area: float | None = None
    minimum_frontage: float | None = None
    maximum_fsr: float | None = Field(default=None, ge=0)
    maximum_units: int | None = Field(default=None, ge=0)
    maximum_height: float | None = Field(default=None, ge=0)
    setbacks: dict[str, float | str | None] = Field(default_factory=dict)
    lot_coverage: float | None = Field(default=None, ge=0)
    parking: str | None = None
    tree_constraints: str | None = None
    servicing_constraints: str | None = None
    character_rules: str | None = None
    theoretical_buildable_sqft: float | None = Field(default=None, ge=0)
    realistic_buildable_sqft: float | None = Field(default=None, ge=0)
    buildability_score: float | None = Field(default=None, ge=0, le=100)
    zoning_threshold_advantages: list[str] = Field(default_factory=list)

    nearest_major_road: str | None = None
    distance_to_major_road_m: float | None = Field(default=None, ge=0)
    property_fronts_major_road: bool | None = None
    road_class: RoadClass | str | None = None
    road_exposure_level: int | None = Field(default=None, ge=1, le=5)
    road_buffer_type: str | None = None
    road_buffer_stability: str | None = None
    buffer_at_risk: bool | None = None
    indoor_noise_mitigation_potential: float | None = Field(default=None, ge=0, le=100)
    outdoor_noise_exposure: float | None = Field(default=None, ge=0, le=100)
    noise_risk: str | None = None

    toa: bool | None = None
    toa_status: str | None = None
    toa_tier: str | None = None
    theoretical_height: float | None = None
    theoretical_fsr: float | None = None
    current_zoning: str | None = None
    rezoning_requirement: str | None = None
    rental_requirement: str | None = None
    below_market_requirement: str | None = None
    distance_to_skytrain_m: float | None = Field(default=None, ge=0)
    assembly_requirement: str | None = None
    assembly_score: float | None = Field(default=None, ge=0, le=100)
    neighbouring_lot_context: str | None = None
    recent_assembly_evidence: str | None = None
    rezoning_application_evidence: str | None = None
    heritage_register: bool | None = None
    heritage_status: str | None = None
    character_status: str | None = None
    retention_incentives: str | None = None
    demolition_rules: str | None = None
    infill_options: str | None = None

    official_elementary_catchment: str | None = None
    official_secondary_catchment: str | None = None
    elementary_distance: float | None = Field(default=None, ge=0)
    secondary_distance: float | None = Field(default=None, ge=0)
    kindergarten_capacity: str | None = None
    overflow: str | None = None
    school_plan: str | None = None
    enrolment: str | None = None
    literacy: str | None = None
    numeracy: str | None = None
    special_programs: list[str] = Field(default_factory=list)
    capital_projects: str | None = None
    seismic_upgrade: str | None = None
    nearby_private_schools: list[str] = Field(default_factory=list)
    current_school_score: float | None = Field(default=None, ge=0, le=100)
    school_future_score: float | None = Field(default=None, ge=0, le=100)
    family_location_score: float | None = Field(default=None, ge=0, le=100)
    buyer_pool_score: float | None = Field(default=None, ge=0, le=100)
    affordability_score: float | None = Field(default=None, ge=0, le=100)
    liquidity_score: float | None = Field(default=None, ge=0, le=100)

    duplex_area: float | None = Field(default=None, ge=0)
    max_duplex_area: float | None = Field(default=None, ge=0)
    estimated_front_unit_area: float | None = Field(default=None, ge=0)
    estimated_rear_unit_area: float | None = Field(default=None, ge=0)
    multiplex_area: float | None = Field(default=None, ge=0)
    max_realistic_multiplex_units: int | None = Field(default=None, ge=0)
    best_economic_product: str | None = None
    median_sold_price: int | None = Field(default=None, ge=0)
    median_lot_size: float | None = Field(default=None, ge=0)
    median_house_size: float | None = Field(default=None, ge=0)
    median_price_per_lot_sqft: float | None = Field(default=None, ge=0)
    median_assessment_ratio: float | None = None
    neighbourhood_comparable_median: int | None = Field(default=None, ge=0)
    neighbourhood_benchmark_count: int | None = Field(default=None, ge=0)
    neighbourhood_benchmark_window_days: int | None = Field(default=None, ge=0)
    neighbourhood_benchmark_confidence: str | None = None
    comp_window_days: int | None = Field(default=None, ge=0)
    comp_count: int | None = Field(default=None, ge=0)
    comp_confidence: str | None = None
    comparable_valuation_status: str = "UNKNOWN"
    expected_market_value: int | None = Field(default=None, ge=0)
    price_dislocation_pct: float | None = None
    price_safety_margin_pct: float | None = None
    relative_affordability_index: float | None = None
    land_value_proxy: int | None = None
    current_use_building_value: int | None = Field(default=None, ge=0)
    estimated_current_use_building_value: int | None = Field(default=None, ge=0)
    current_use_building_value_method: str | None = None
    adjusted_land_price_per_sqft: float | None = None
    land_discount_or_premium_pct: float | None = None
    comparable_conflict_count: int = Field(default=0, ge=0)
    comparable_conflict_ids: list[str] = Field(default_factory=list)
    annual_municipal_carrying_cost: int | None = Field(default=None, ge=0)
    pure_property_tax: int | None = Field(default=None, ge=0)
    water: int | None = Field(default=None, ge=0)
    sewer: int | None = Field(default=None, ge=0)
    garbage: int | None = Field(default=None, ge=0)
    drainage_charge: int | None = Field(default=None, ge=0)
    other_levies: int | None = Field(default=None, ge=0)
    tax: int | None = Field(default=None, ge=0)
    utilities: int | None = Field(default=None, ge=0)

    live_score: float | None = Field(default=None, ge=0, le=100)
    land_score: float | None = Field(default=None, ge=0, le=100)
    investment_score: float | None = Field(default=None, ge=0, le=100)
    land_to_product_score: float | None = Field(default=None, ge=0, le=100)
    total_score: float | None = Field(default=None, ge=0, le=100)
    total_score_label: str = "UNKNOWN"
    data_confidence_score: float | None = Field(default=None, ge=0, le=100)
    data_confidence_band: str | None = None
    data_verification_status: str = "UNKNOWN"
    manual_verification_required: bool = False
    manual_verification_reasons: list[str] = Field(default_factory=list)
    provisional_source_registry_ids: list[str] = Field(default_factory=list)
    candidate_status: CandidateStatus = CandidateStatus.NEED_VERIFICATION
    opportunity_tags: list[str] = Field(default_factory=list)
    data_conflict: bool = False
    data_reconciliation_notes: list[str] = Field(default_factory=list)
    last_updated: datetime | None = None

    def to_flat_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", by_alias=True)
        data["PID"] = self.pid
        data["number_of_PIDs"] = self.number_of_pids
        data["lot_shape"] = self.lot_shape.value if isinstance(self.lot_shape, LotShape) else self.lot_shape
        data["shape"] = data["lot_shape"]
        data["lane"] = self.lane if self.lane is not None else self.lane_access
        data["corner"] = self.corner if self.corner is not None else self.corner_lot
        data["TOA"] = self.toa if self.toa is not None else self.toa_status
        data["max_FSR"] = self.maximum_fsr
        data["max_units"] = self.maximum_units
        data["road_distance"] = self.distance_to_major_road_m
        data["distance_to_SkyTrain"] = self.distance_to_skytrain_m
        return data


class RedevelopmentScenario(_ResearchModel):
    property_id: str
    scenario_type: ScenarioType
    legal_status: str = "UNKNOWN"
    zoning_basis: str | None = None
    bylaw_version: str | None = None
    unit_count: int | None = Field(default=None, ge=0)
    total_buildable_sqft: float | None = Field(default=None, ge=0)
    realistic_buildable_sqft: float | None = Field(default=None, ge=0)
    estimated_saleable_sqft: float | None = Field(default=None, ge=0)
    average_unit_size: float | None = Field(default=None, ge=0)
    likely_bedrooms: str | None = None
    likely_buyer: str | None = None
    layout_options: list[str] = Field(default_factory=list)
    front_building_noise_buffer_hypothesis: bool | None = None
    estimated_front_unit_area: float | None = Field(default=None, ge=0)
    estimated_rear_unit_area: float | None = Field(default=None, ge=0)
    product_options: list[dict[str, Any]] = Field(default_factory=list)
    best_product_option: dict[str, Any] | None = None
    estimated_resale_price: int | None = Field(default=None, ge=0)
    hard_cost: int | None = Field(default=None, ge=0)
    soft_cost: int | None = Field(default=None, ge=0)
    developer_specific_cost: int | None = Field(default=None, ge=0)
    contingency: int | None = Field(default=None, ge=0)
    total_project_cost: int | None = Field(default=None, ge=0)
    execution_complexity: float | None = Field(default=None, ge=0, le=100)
    product_market_fit: float | None = Field(default=None, ge=0, le=100)
    existing_property_market_value: int | None = Field(default=None, ge=0)
    new_finished_market_value: int | None = Field(default=None, ge=0)
    redevelopment_cost: int | None = Field(default=None, ge=0)
    future_opportunity_cost: int | None = None
    redevelopment_uplift: int | None = None
    estimated_project_profit: int | None = None
    economic_gate_pass: bool | None = None
    exit_mode: str = "ALL_SALE_BASELINE"
    owner_occupancy_benefit: int | None = None
    financing_not_modeled: bool = True
    assumptions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class ScoreComponent(_ResearchModel):
    points: float = Field(ge=0)
    maximum_points: float = Field(ge=0)
    evidence_status: EvidenceStatus = EvidenceStatus.CONFIRMED
    explanation: str
    unknown_fields: list[str] = Field(default_factory=list)


class DimensionScore(_ResearchModel):
    points: float = Field(ge=0)
    maximum_points: float = Field(ge=0)
    label: str = "UNKNOWN"
    components: dict[str, ScoreComponent] = Field(default_factory=dict)


class ScoreSnapshot(_ResearchModel):
    property_id: str
    generated_at: datetime
    scoring_version: str
    config_hash: str
    weekly_run_id: int | None = None
    source_run_id: int | None = None
    source_registry_id: str | None = None
    total_score: float = Field(ge=0, le=100)
    total_score_label: str = "UNKNOWN"
    live_score: float = Field(ge=0, le=100)
    land_score: float = Field(ge=0, le=100)
    investment_score: float = Field(ge=0, le=100)
    land_to_product_score: float = Field(default=0, ge=0, le=100)
    data_confidence_score: float = Field(ge=0, le=100)
    data_confidence_band: str
    dimensions: dict[str, DimensionScore] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    opportunity_tags: list[str] = Field(default_factory=list)
    candidate_status: CandidateStatus | None = None
    state_reasons: list[str] = Field(default_factory=list)


class PropertyObservation(_ResearchModel):
    observation_id: str
    property_id: str | None = None
    observed_at: datetime
    source_registry_id: str
    raw_payload: dict[str, Any]
    parser_version: str
    normalized_record: PropertyRecord
    normalization_warnings: list[str] = Field(default_factory=list)


class WatchlistEvent(_ResearchModel):
    property_id: str
    event_type: str
    created_at: datetime
    weekly_run_id: int | None = None
    source_run_id: int | None = None
    source_registry_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class VerificationItem(_ResearchModel):
    verification_id: str
    property_id: str
    source_registry_id: str
    source_url: str
    status: str
    reason: str
    match_method: str | None = None
    raw_snapshot_path: str | None = None
    observed_at: datetime
    weekly_run_id: int | None = None
    source_run_id: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)
