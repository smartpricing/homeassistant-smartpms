# SmartPMS per Home Assistant

Integrazione custom per [Home Assistant](https://www.home-assistant.io/) che espone lo stato di occupazione delle camere dal sistema [SmartPMS](https://pms-api.smartness.com).

## Funzionalità

- Sensore per ogni unità/camera con stato: `free`, `occupied`, `blocked`
- Icona dinamica in base allo stato
- Aggiornamento automatico configurabile (default: 5 minuti)
- Configurazione tramite UI (Config Flow)
- Compatibile con HACS

## Installazione

### HACS (consigliato)

1. Apri HACS in Home Assistant
2. Vai su "Integrazioni" → menu ⋮ → "Repository personalizzati"
3. Aggiungi l'URL di questo repository come tipo "Integrazione"
4. Cerca "SmartPMS" e installalo
5. Riavvia Home Assistant

### Manuale

1. Copia la cartella `custom_components/smartpms/` in `/config/custom_components/`
2. Riavvia Home Assistant

## Configurazione

1. Vai su Impostazioni → Dispositivi e Servizi → Aggiungi Integrazione
2. Cerca "SmartPMS"
3. Inserisci:
   - **Email**: email dell'account SmartPMS
   - **Password**: password dell'account
   - **Chiave API**: la chiave API partner

## Opzioni

Dopo la configurazione, puoi modificare l'intervallo di aggiornamento:
- Impostazioni → Dispositivi e Servizi → SmartPMS → Configura
- Intervallo minimo: 60 secondi, massimo: 3600 secondi

## Entità create

Per ogni unità viene creato un sensore `sensor.smartpms_<property_id>_<unit_id>` con:
- **Stato**: `free`, `occupied`, `blocked`
- **Attributi**: `unit_id`, `unit_name`, `property_id`
- **Icona**: cambia in base allo stato
