"""Explicit mode policy for the existing coordinator; no networking or second engine."""
import hashlib,json,time
POWER_ENTITY='switch.panasonic_heat_pump_main_heatpump_state'
MODES={'heat':('Solo caldo',0,'Heat'),'cool':('Solo freddo',1,'Cool'),
       'heat_dhw':('Caldo + ACS',4,'Heat+DHW'),'cool_dhw':('Freddo + ACS',5,'Cool+DHW'),
       'off':('OFF',None,None)}

def fingerprint(settings,states,telemetry,attestation):
    values={k:states.get(e,{}).get('state') for k,e in {**{k:telemetry[k] for k in ('mode','zones')},'power':POWER_ENTITY}.items()}
    raw={'settings':settings,'values':values,'commissioning':attestation}
    return hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def validate_mode(mode,settings,attestation,states):
    if mode not in MODES:raise ValueError('Modalità non supportata')
    if states.get(POWER_ENTITY,{}).get('state') not in ('on','off'):raise ValueError('Stato ON/OFF Panasonic non disponibile')
    # OFF and subsequent re-enable require the already commissioned circulation path.
    if not (attestation.get('hydraulic_layout')=='buffer_secondary_bypass' and all(attestation.get(k) is True for k in ('hydraulic_path_verified','secondary_pump_verified','safe_closure_with_pump'))):raise ValueError('Percorso buffer e chiusura sicura non confermati')
    if mode!='off':
        season='summer' if mode.startswith('cool') else 'winter'
        if settings['season']!=season:raise ValueError('Modalità incompatibile con la stagione del Coordinatore')
        if not settings['master'] or not any(f['enabled'] for f in settings['floors'].values()):raise ValueError('Abilitare prima il Coordinatore e almeno un piano')
        if season=='summer' and not attestation.get('fan_coils_verified'):raise ValueError('Fan-coil estivi non ancora collaudati')
    return MODES[mode]

def capability(settings,attestation,states,telemetry,blockers):
    options={}
    for key,(label,_,_) in MODES.items():
        try:
            validate_mode(key,settings,attestation,states)
            if blockers:raise ValueError('; '.join(blockers))
            reason=None
        except ValueError as e:reason=str(e)
        options[key]={'label':label,'available':reason is None,'reason':reason}
    return {'version':1,'options':options,'baseline':fingerprint(settings,states,telemetry,attestation),'at':time.time(),
            'requested':settings.get('pdc_mode'),'transition':settings.get('pdc_mode_transition')}
