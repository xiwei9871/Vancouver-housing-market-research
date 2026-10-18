from northshore_watchdog.rentals.collectors import RentalSnapshotCollector


def test_rental_snapshot_collector_parses_realtor_and_zolo_cards(tmp_path):
    snapshot = tmp_path / "rentals.html"
    snapshot.write_text(
        """
        <article class="rental-card" data-address="8080 Lansdowne Road" data-city="Richmond"
          data-neighbourhood="Lansdowne" data-url="https://www.realtor.ca/real-estate/123/richmond-rental"
          data-rent="$2,900" data-bedrooms="2" data-bathrooms="2" data-property-type="Condo"
          data-transit-minutes="6" data-amenities="gym,pool,elevator,in-suite laundry">
          Bright condo near SkyTrain and T&T.
        </article>
        <article class="rental-card" data-address="123 Main Street" data-city="Vancouver"
          data-neighbourhood="Yaletown" data-url="https://www.zolo.ca/vancouver-real-estate/123-main-street"
          data-rent="$3,250" data-bedrooms="2" data-bathrooms="1" data-property-type="Apartment">
          Apartment with gym.
        </article>
        <article class="rental-card" data-address="999 Bad Source" data-city="Richmond"
          data-url="https://example.com/listing/1" data-rent="$2,800" data-bedrooms="2" data-bathrooms="2">
        </article>
        """,
        encoding="utf-8",
    )

    listings = RentalSnapshotCollector({
        "input_dirs": [str(tmp_path)],
        "allowed_domains": ["realtor.ca", "zolo.ca"],
    }).collect()

    assert [listing.address for listing in listings] == ["8080 Lansdowne Road", "123 Main Street"]
    assert listings[0].monthly_rent == 2900
    assert listings[0].amenities == ["gym", "pool", "elevator", "in-suite laundry"]
    assert listings[0].transit_minutes == 6


def test_rental_snapshot_collector_extracts_plain_links_as_manual_review(tmp_path):
    snapshot = tmp_path / "links.txt"
    snapshot.write_text(
        "https://www.realtor.ca/real-estate/789/222-richmond-rental\nhttps://example.com/skip",
        encoding="utf-8",
    )

    listings = RentalSnapshotCollector({
        "input_dirs": [str(tmp_path)],
        "allowed_domains": ["realtor.ca", "zolo.ca"],
    }).collect()

    assert len(listings) == 1
    assert listings[0].url == "https://www.realtor.ca/real-estate/789/222-richmond-rental"
    assert listings[0].address == "222 richmond rental"


def test_rental_snapshot_collector_preserves_snapshot_provenance(tmp_path):
    snapshot = tmp_path / "fresh.html"
    snapshot.write_text(
        """
        <article class="rental-card" data-address="8080 Lansdowne Road" data-city="Richmond"
          data-url="https://www.realtor.ca/real-estate/123/richmond-rental"
          data-rent="$2,900" data-bedrooms="2" data-bathrooms="2"></article>
        """,
        encoding="utf-8",
    )

    listings = RentalSnapshotCollector({
        "selected_files": [str(snapshot)],
        "allowed_domains": ["realtor.ca", "zolo.ca"],
    }).collect()

    assert listings[0].last_seen_date is not None
    assert listings[0].raw_data["snapshot_path"] == str(snapshot)
    assert listings[0].raw_data["snapshot_observed_at"] == listings[0].last_seen_date.isoformat()
    assert listings[0].raw_data["snapshot_sha256"]
