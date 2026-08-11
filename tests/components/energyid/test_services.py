"""Tests for EnergyID directive actions."""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientError
from energyid_webhooks.directives import (
    DirectiveData,
    DirectiveResource,
    DirectiveSignal,
    SignalProvider,
)
import pytest

from homeassistant.components.energyid.const import CONF_ENABLE_DIRECTIVES, DOMAIN
from homeassistant.components.energyid.sensor import (
    SERVICE_GET_DIRECTIVE_SCHEDULE,
    EnergyIDDirectiveSensor,
)
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.util import dt as dt_util

from tests.common import MockConfigEntry

DIRECTIVE_ID = "11111111-1111-1111-1111-111111111111"


def _directive_fixture() -> tuple[DirectiveResource, DirectiveData]:
    """Return one EnergyID directive schedule."""
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    resource = DirectiveResource(
        id=DIRECTIVE_ID,
        title="Météo MALOEN",
        description="Community balance forecast",
        properties=("color", "signal"),
        signal_provider=SignalProvider(
            id="epv",
            display_name="EPV",
            logo_url="https://example.com/epv.svg",
        ),
    )
    schedule = DirectiveData(
        title=resource.title,
        description=resource.description,
        interval="PT15M",
        data=(
            DirectiveSignal(
                timestamp=now,
                signal="++",
                color="#00750e",
                raw_value=1.0,
            ),
            DirectiveSignal(
                timestamp=now + dt.timedelta(minutes=15),
                signal="0",
                color="#EBEBEB",
                raw_value=0.0,
            ),
        ),
    )
    return resource, schedule


async def test_get_directive_schedule(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webhook_client: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test the action returns the complete directive schedule."""
    resource, schedule = _directive_fixture()
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_ENABLE_DIRECTIVES: True}
    )
    mock_webhook_client.api_access_token = "device-token"
    mock_webhook_client.get_directives = AsyncMock(return_value=[resource])
    mock_webhook_client.get_directive_data = AsyncMock(return_value=schedule)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    [registry_entry] = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_DIRECTIVE_SCHEDULE,
        {},
        target={ATTR_ENTITY_ID: registry_entry.entity_id},
        blocking=True,
        return_response=True,
    )

    returned = response[registry_entry.entity_id]
    assert returned["title"] == "Météo MALOEN"
    assert returned["provider"]["display_name"] == "EPV"
    assert returned["interval"] == "PT15M"
    assert returned["data"] == [
        {
            "timestamp": schedule.data[0].timestamp.isoformat(),
            "signal": "++",
            "color": "#00750e",
            "raw_value": 1.0,
        },
        {
            "timestamp": schedule.data[1].timestamp.isoformat(),
            "signal": "0",
            "color": "#EBEBEB",
            "raw_value": 0.0,
        },
    ]
    assert schedule.data[1].timestamp - schedule.data[0].timestamp == dt.timedelta(
        minutes=15
    )
    # The schedule is served from the coordinator snapshot; only the
    # coordinator refresh during setup contacted EnergyID.
    assert mock_webhook_client.get_directive_data.await_count == 1


async def test_get_directive_schedule_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webhook_client: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test the action fails cleanly when the schedule could not be fetched."""
    resource, _ = _directive_fixture()
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_ENABLE_DIRECTIVES: True}
    )
    mock_webhook_client.api_access_token = "device-token"
    mock_webhook_client.get_directives = AsyncMock(return_value=[resource])
    mock_webhook_client.get_directive_data = AsyncMock(
        side_effect=ClientError("upstream failed")
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    [registry_entry] = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    # The entity is unavailable, so entity service targeting skips it;
    # exercise the schedule guard on the entity itself.
    component: EntityComponent[SensorEntity] = hass.data["entity_components"]["sensor"]
    entity = component.get_entity(registry_entry.entity_id)
    assert isinstance(entity, EnergyIDDirectiveSensor)
    with pytest.raises(HomeAssistantError):
        await entity.async_get_directive_schedule()
