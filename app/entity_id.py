"""Single source of truth for the ``<domain>.<name>`` entity-id join key.

Two paths need to agree on how an entity id is built: the **manifest** side
(``registry.py``, which announces the devices that exist) and the **telemetry**
side (``mqtt_transport.py``, which tags each sample with the entity it came
from). Both derive the ``domain`` half from the integration's class name in
exactly the same way, so that logic lives here rather than being duplicated —
if the two ever diverged, telemetry would stop joining to its manifest entity.

Pure functions, no IO.
"""


def derive_domain(integration_name: str) -> str:
    """Derive an entity ``domain`` from an integration class name.

    Strips a trailing ``Integration`` suffix (case-sensitive, exactly that
    literal) and lowercases the remainder:

        ``GPIOIntegration`` -> ``gpio``
        ``MQTTIntegration`` -> ``mqtt``
        ``DHTSensor``       -> ``dhtsensor`` (no suffix to strip)
    """
    name = integration_name
    if name.endswith("Integration"):
        name = name[: -len("Integration")]
    return name.lower()


def derive_entity_id(integration_name: str, name: str) -> str:
    """Build the ``<domain>.<name>`` entity id for a device.

    ``domain`` comes from :func:`derive_domain`; ``name`` is the device's local
    name as registered by its integration.
    """
    return f"{derive_domain(integration_name)}.{name}"
