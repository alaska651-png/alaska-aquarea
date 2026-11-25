"""Unified snapshot; quality rules adapted from climate-data.mjs and ha-data.js."""
import copy, json, math, time
from datetime import datetime, timezone


def stamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def numeric(value):
    if isinstance(value, bool) or value in (None, '', 'unknown', 'unavailable'):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def reading(value=None, unit=None, source=None, received=None, measured=None, fresh=False, valid=True):
    quality = 'unavailable' if value is None or not valid else 'fresh' if fresh else 'stale'
    return {'value': value if valid else None, 'unit': unit, 'quality': quality, 'source': source,
            'received_at': received, 'measured_at': measured,
            'timestamp_meaning': 'receipt; sensor acquisition time unknown' if measured is None else 'HA last_reported/last_updated'}


class Model:
    def __init__(self, cfg, catalog):
        self.cfg, self.catalog = cfg, {p['name']: p for p in catalog}
        self.catalog['Buffer_Tank_Delta'] = {'unit': 'K', 'topic': 'TOP113'}
        self.http, self.messages, self.states = {}, {}, {}
        self.links = {'heishamon': False, 'home_assistant': False, 'mqtt': False}
        self.http_at = self.ha_at = self.frame_at = self.last_count = None
        self.coordinator_data, self.coordinator_at = {}, None
        self.lwt = None

    def fail(self, source):
        self.links[source] = False

    def pdc(self, status, payload, now):
        count = status.get('pdc_good_reads')
        if type(count) is int:
            if self.last_count is not None and count > self.last_count:
                self.frame_at = now
            elif self.last_count is not None and count < self.last_count:
                self.frame_at = None
            self.last_count = count
        else:
            self.frame_at = None
        self.http = {p['Name']: p.get('Value') for p in payload.get('heatpump', []) if p.get('Name') in self.catalog}
        self.http_at, self.links['heishamon'] = now, True

    def ha(self, states, now):
        self.states = {s['entity_id']: s for s in states}
        self.ha_at, self.links['home_assistant'] = now, True

    def coordinator(self, data, now):
        self.coordinator_data = copy.deepcopy(data)
        self.coordinator_at = now

    def mqtt_link(self, connected):
        self.links['mqtt'] = connected
        if not connected:
            self.lwt = None

    def mqtt_message(self, topic, value, retained, now):
        base = self.cfg.get('mqtt_prefix', 'panasonic_heat_pump').strip('/')
        if topic == base + '/LWT':
            self.lwt = value
        elif topic.startswith(base + '/main/') and topic[len(base + '/main/'):] in self.catalog:
            self.messages[topic[len(base + '/main/'):]] = (value, retained, now)

    @staticmethod
    def age_ok(at, now, limit):
        return at is not None and -5 <= now - at < limit

    def metric(self, key, now):
        meta = self.catalog.get(key, {})
        live = self.links['heishamon'] and self.age_ok(self.http_at, now, 15) and self.age_ok(self.frame_at, now, 20)
        packet = self.messages.get(key)
        mqtt_live = packet and not packet[1] and self.links['mqtt'] and self.lwt == 'Online' and live and self.age_ok(packet[2], now, 30)
        if mqtt_live:
            raw, _, at = packet
            source, fresh = 'mqtt', True
        elif key in self.http:
            raw, at, source, fresh = self.http[key], self.http_at, 'heishamon_http', live
        elif packet:
            raw, _, at = packet
            source, fresh = 'mqtt_retained_or_old', False
        else:
            raw, at, source, fresh = None, None, None, False
        value = numeric(raw)
        unit = meta.get('unit')
        if value is None and raw is not None and unit == '':
            value = str(raw) if str(raw) not in ('unknown', 'unavailable', '') else None
        valid = not (unit == '°C' and (not isinstance(value, (int, float)) or value < -50 or value > 100))
        if key == 'Heat_Pump_Model' and value == 255:
            valid = False
        return reading(value, unit, source, at, fresh=fresh, valid=valid)

    def entity(self, entity, now, kind=None, max_age=7200):
        state = self.states.get(entity, {})
        attrs = state.get('attributes', {})
        measured = stamp(state.get('last_reported') or state.get('last_updated'))
        raw, unit = state.get('state'), attrs.get('unit_of_measurement')
        value = numeric(raw) if kind else raw
        valid = raw not in (None, '', 'unknown', 'unavailable')
        if kind:
            from_unit = unit
            if kind == 'temperature':
                value = (value - 32) * 5 / 9 if value is not None and unit == '°F' else value if unit == '°C' else None
                unit, valid = '°C', value is not None and -50 <= value <= 100
            elif kind == 'humidity':
                valid = value is not None and from_unit == '%' and 0 <= value <= 100
            elif kind in ('power', 'energy'):
                allowed = ('W', 'kW') if kind == 'power' else ('Wh', 'kWh')
                valid = value is not None and from_unit in allowed and value >= 0
                value = value / 1000 if valid and from_unit in ('W', 'Wh') else value
                unit = 'kW' if kind == 'power' else 'kWh'
        fresh = self.links['home_assistant'] and self.age_ok(self.ha_at, now, 25) and self.age_ok(measured, now, max_age)
        return reading(value, unit, 'home_assistant', self.ha_at, measured, fresh, valid)

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        metrics = {key: self.metric(key, now) for key in self.catalog}
        coordinator_fresh = self.age_ok(self.coordinator_at, now, 45)
        event = self.coordinator_data
        snapshot = event.get('snapshot', {})
        mapping = snapshot.get('mapping', {})
        rooms = self.cfg.get('rooms') or mapping.get('rooms', {})
        room_result = {}
        for key, room in rooms.items():
            plan = snapshot.get('plan', {}).get('rooms', {}).get(key, {})
            room_result[key] = {'name': room.get('name', key), 'floor': room.get('floor'),
                                'temperature': self.entity(room.get('sensor'), now, 'temperature'),
                                'humidity': self.entity(room.get('humidity'), now, 'humidity'),
                                'relay': self.entity(room.get('relay'), now),
                                'setpoint': reading(plan.get('setpoint'), '°C', 'coordinator_event', self.coordinator_at, fresh=coordinator_fresh),
                                'demand': reading(plan.get('desired'), source='coordinator_event', received=self.coordinator_at, fresh=coordinator_fresh),
                                'operating_state': reading(plan.get('state'), source='coordinator_event', received=self.coordinator_at, fresh=coordinator_fresh)}
        season = snapshot.get('settings', {}).get('season') if coordinator_fresh else None
        weather = self.states.get(self.cfg.get('entities', {}).get('weather', 'weather.forecast_casa'), {})
        rh = numeric(weather.get('attributes', {}).get('humidity'))
        weather_at = stamp(weather.get('last_updated'))
        weather_rh = reading(rh, '%', 'weather_service_not_local_probe', self.ha_at, weather_at,
                             self.links['home_assistant'] and self.age_ok(self.ha_at, now, 25) and self.age_ok(weather_at, now, 10800),
                             rh is not None and 0 <= rh <= 100 and weather.get('state') not in ('unknown', 'unavailable'))
        telemetry = mapping.get('telemetry', {})
        def pump(n):
            r = self.entity(self.cfg.get('entities', {}).get('pump' + str(n)) or telemetry.get('pump' + str(n)), now)
            heartbeat = self.states.get(self.cfg.get('entities', {}).get('heartbeat') or telemetry.get('heartbeat'), {})
            online = self.states.get(self.cfg.get('entities', {}).get('online') or telemetry.get('online'), {})
            # Port of climate-data.mjs pumpStatus: heartbeat 40s, explicit ON/OFF.
            if r['value'] not in ('on', 'off'):
                r.update(value=None, quality='unavailable')
            elif not (self.links['home_assistant'] and self.age_ok(self.ha_at, now, 25) and online.get('state') == 'on' and self.age_ok(stamp(heartbeat.get('last_changed')), now, 40)):
                r['quality'] = 'stale'
            else:
                r['quality'] = 'fresh'
            return r
        energy = {}
        configured = self.cfg.get('entities', {})
        for service in ('heat', 'dhw', 'cool'):
            power = {}
            for direction in ('consumption', 'production'):
                key = service + '_' + direction
                default = 'sensor.panasonic_heat_pump_main_' + service + '_power_' + direction
                power[direction] = self.entity(configured.get(key, default), now, 'power', 120)
            a, b = power['production'], power['consumption']
            aligned = a['measured_at'] is not None and b['measured_at'] is not None and abs(a['measured_at'] - b['measured_at']) <= 30
            valid = a['quality'] == b['quality'] == 'fresh' and aligned and b['value'] > 0
            power['ratio'] = reading(a['value'] / b['value'] if valid else None, 'EER' if service == 'cool' else 'COP', 'derived_aligned_power', self.ha_at, fresh=valid)
            power['electric_total'] = self.entity(configured.get(service + '_total'), now, 'energy', 7200)
            energy[service] = power
        return {'api_version': '1', 'generated_at': now, 'read_only': True,
                'sources': {k: {'connected': v, 'fresh': bool(v and self.age_ok({'heishamon': self.http_at, 'home_assistant': self.ha_at, 'mqtt': max((x[2] for x in self.messages.values()), default=None)}[k], now, 25 if k == 'home_assistant' else 30))} for k, v in self.links.items()}, 'temperatures': {k: metrics[k] for k in ('Outside_Temp', 'Main_Inlet_Temp', 'Main_Outlet_Temp')},
                'humidity': {'outdoor_weather': weather_rh, 'rooms': {k: r['humidity'] for k, r in room_result.items()}}, 'rooms': room_result,
                'zones': {'season': season, 'season_quality': 'fresh' if coordinator_fresh and season else 'unavailable',
                          'winter_zone': 2, 'summer_zone': 1, 'active': metrics.get('Zones_State'),
                          'zone1': {'use': 'fan_coil_summer', 'temperature': metrics.get('Z1_Water_Temp'), 'target': metrics.get('Z1_Water_Target_Temp')},
                          'zone2': {'use': 'underfloor_mixed_winter', 'temperature': metrics.get('Z2_Water_Temp'), 'target': metrics.get('Z2_Water_Target_Temp')}},
                'dhw': {'temperature': metrics.get('DHW_Temp'), 'target': metrics.get('DHW_Target_Temp'), 'differential': metrics.get('DHW_Heat_Delta')},
                'buffer': {'temperature': metrics.get('Buffer_Temp'), 'differential': metrics['Buffer_Tank_Delta'], 'secondary_flow': reading(unit='l/min')},
                'pumps': {'zone1': pump(1), 'zone2': pump(2), 'primary_flow': metrics.get('Pump_Flow')},
                'energy': energy, 'operating': {'mode': metrics.get('Operating_Mode_State'),
                    'coordinator': reading(event.get('status'), source='coordinator_event', received=self.coordinator_at, fresh=coordinator_fresh),
                    'master': reading(snapshot.get('settings', {}).get('master'), source='coordinator_event', received=self.coordinator_at, fresh=coordinator_fresh),
                    'schedule_enabled': reading(snapshot.get('settings', {}).get('schedule_enabled'), source='coordinator_event', received=self.coordinator_at, fresh=coordinator_fresh)},
                'metrics': metrics}
