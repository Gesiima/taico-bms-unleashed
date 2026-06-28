# V-KING / TAICO BMS — Protokoll-Spezifikation (reverse-engineered)

**Gerät:** VK48150 (48-V-Klasse, 150 Ah, 16 LiFePO4-Zellen) · Firmware `VK51100_DGGZ_V15.53` · Hersteller-Label TAICO / V-KING NER
**Protokollfamilie:** PACE / Pylontech / Seplos (YD/T 1363.3), herstellereigene Version `0x52`
**Stand:** vollständig verifiziert gegen Live-Mitschnitte und GUI-Werte. Diese Spezifikation wurde durch Analyse der mitgelieferten Software (`VkSerialDll.dll`) und Schnittstellen-Mitschnitten erstellt; sie dient als Grundlage für eine eigene Implementierung.

---

## 1. Rahmenformat (Frame)

ASCII-Rahmen, jedes Daten-Byte wird als **zwei ASCII-Hex-Zeichen** übertragen.

```
~  VER  ADR  CID1  CID2  LENGTH   INFO        CHKSUM  CR
7E "52" "NN" "46"  "xx"  4 Zeichen 2·n Zeichen 4 Zeichen 0D
```

| Feld | Bytes/Zeichen | Wert |
|---|---|---|
| SOI | 1 | `0x7E` (`~`) |
| VER | 2 ASCII | `52` (herstellereigene Version) |
| ADR | 2 ASCII | Pack-Adresse `01`–`08` |
| CID1 | 2 ASCII | `46` (Batterie) |
| CID2 | 2 ASCII | Befehlscode (siehe §2) |
| LENGTH | 4 ASCII | LCHKSUM-Nibble + 12-Bit-LENID (= Anzahl ASCII-Zeichen im INFO) |
| INFO | 2·n ASCII | Nutzdaten (Hex-kodiert) |
| CHKSUM | 4 ASCII | Prüfsumme (siehe unten) |
| EOI | 1 | `0x0D` (CR) |

**Prüfsumme:** Summe der ASCII-Codes aller Zeichen zwischen SOI und CHKSUM, modulo 65536, Zweierkomplement (`(~sum + 1) & 0xFFFF`), als 4 Hex-Zeichen.

**LENGTH-Feld:** LENID = Anzahl der ASCII-Zeichen im INFO. Length-Checksum-Nibble = `(~((d0+d1+d2)) + 1) & 0xF` über die drei Hex-Stellen von LENID.

**Beispiel** (Analogwerte Pack 1 lesen): `~52014642E00201FD30\r`
Antwort beginnt mit `~520146 00 …` (RTN `00` = OK).

---

## 2. Befehlsübersicht (CID2)

| CID2 | Funktion | Richtung | INFO-Anfrage |
|---|---|---|---|
| `42` | Analogwerte (Live) | lesen | Pack (1 B) |
| `44` | Status / Warnung / Schutz | lesen | Pack (1 B) |
| `47` | Parameter | lesen | Pack (1 B) |
| `49` | Parameter | schreiben | Pack + 166 B |
| `4B` | History-Records | lesen (seitenweise) | Modus + Pack |
| `4C` | Warning-/Alarm-Records | lesen (seitenweise) | Modus + Pack |
| `4D` | Echtzeituhr (RTC) | lesen | Pack |
| `4E` | Echtzeituhr (RTC) | schreiben | Pack + Zeit |
| `E2` | MOS / Strombegrenzung steuern | schreiben | Pack + 2-B-Maske |
| `E5` | Kapazität | lesen | Pack |
| `E9` | Firmware-/Versionsstring | lesen | Pack |
| `EB` | Total Discharge (kumuliert) | lesen | Pack |
| `ED` | Kalibrierung (Strom/Temp) | lesen | Pack + Gruppe + Item |
| `EF` | Power Off (Abschalten/Sleep) | schreiben | Pack |
| `F1` | Produktinformationen | lesen | Pack |
| `F2` | Produktinformationen | schreiben | Pack + 210 B ASCII |

> Schreib-Gegenstücke für Kapazität, Zyklen, Kalibrierung, Records löschen usw. existieren laut DLL (`WriteCapValue`, `WriteCycleTimes`, `WriteCalInfo`, `WriteClrHistory`, `WriteClrWarning`), sind aber noch nicht per Mitschnitt verifiziert.

---

## 3. Analogwerte (`CID2 = 42`)

INFO-Antwort (Bytes nach dem Rahmen):

| Offset | Feld | Kodierung |
|---|---|---|
| 0 | Dataflag | `0x01` |
| 1 | Zellzahl M | `0x10` = 16 |
| 2 … 2+2M | M × Zellspannung | `u16`, mV (1:1) |
| … | Temp-Zahl N | `0x06` = 6 |
| … | N × Temperatur | `u16` − 40 = °C |
| … | Strom | `s16` × 10 mA  (negativ = Entladen) |
| … | Gesamtspannung | `u16` × 10 mV |
| … | Restkapazität | `u16` × 10 mAh |
| … | (P / reserviert) | 1 B |
| … | Vollladekapazität | `u16` × 10 mAh |
| … | Zyklen | `u16` |
| … | SOC | `u8` % |
| … | SOH | `u8` % |

Temperaturreihenfolge: Cell T1–T4, Env, MOS (6 Sensoren).
*Verifiziert:* 16 Zellen 3352–3458 mV, Strom 5,29 A, Spannung 54,78 V, Restkap. 150,00 Ah, Zyklen 272 — exakt deckungsgleich mit der GUI.

---

## 4. Status / Warnung (`CID2 = 44`)

INFO-Antwort, 46 Bytes:

| Offset | Feld |
|---|---|
| 0 | Dataflag (`0x01`) |
| 1 | Zellzahl M (16) |
| 2 … 17 | je 1 Flag-Byte pro Zelle (0 = OK) |
| 18 | Temp-Zahl N (6) |
| 19 … 24 | je 1 Flag-Byte pro Temperatursensor |
| 25 … 45 | Zustands-/Schutz-/Balance-Bitfelder, FET-Status (CFET/DFET/Vorlade/Lade/Entlade/Strombegrenzung) |

> Die genaue Bit-Bedeutung der Zustandsbytes (Offset 25+) lässt sich vollständig nur mit einem Mitschnitt bei aktivem Alarm/Schutz festlegen. Ohne aktive Warnung sind die meisten Bytes 0.

---

## 5. Parameter (`CID2 = 47` lesen / `49` schreiben)

166 Byte (Lesen) bzw. 167 Byte (Schreiben), Felder **sequenziell** in Index-Reihenfolge. Feldgröße 1 oder 2 Byte. Skalierungsregeln:

- **Temperaturen:** gespeichert = °C **+ 40**
- **Ströme:** Anzeige (mA) = roh **× 10**
- **Pack-Gesamtspannungen:** Anzeige (mV) = roh **× 2**
- **Zell-/Differenzspannungen (mV):** 1:1
- **Verzögerungen (mS):** Anzeige = roh **× 10**
- **Stückzahlen / Enums / Bitmasken:** 1:1

### Vollständige Parametertabelle

| # | Bytes | Parameter | Kodierung |
|---|---|---|---|
| 1 | 1 | PACK Address(1-8) | 1:1 |
| 2 | 1 | Battery node number(1-16) | Anzahl (1:1) |
| 3 | 2 | Current Limiting Startup(A) | mA bzw. mS (roh×10) |
| 4 | 2 | Sampling resistance(0.01mΩ) | 1:1 |
| 5 | 2 | Battery self depletion(mAh) | mV (1:1) |
| 6 | 1 | Balance mode | 1:1 |
| 7 | 1 | Balanced high temperature prohibition(°C) | °C (roh−40) |
| 8 | 1 | Balanced low temperature prohibition(°C) | °C (roh−40) |
| 9 | 2 | Balance starting voltage(mV) | mV (1:1) |
| 10 | 2 | Balance starting pressure difference(mV) | mV (1:1) |
| 11 | 2 | Balance Stop pressure difference(mV) | mV (1:1) |
| 12 | 2 | Cell high voltage alarm(mV) | mV (1:1) |
| 13 | 2 | Cell high voltage alarm recovery(mV) | mV (1:1) |
| 14 | 2 | Cell low voltage alarm (mV) | mV (1:1) |
| 15 | 2 | Cell low voltage alarm recovery(mV) | mV (1:1) |
| 16 | 2 | Pack voltage high voltage alarm(mV) | mV (roh×2) |
| 17 | 2 | Pack voltage high voltage alarm recovery(mV) | mV (roh×2) |
| 18 | 2 | Pack voltage low voltage alarm(mV) | mV (roh×2) |
| 19 | 2 | Pack voltage low voltage alarm recovery(mV) | mV (roh×2) |
| 20 | 1 | Battery high temperature alarm(°C) | °C (roh−40) |
| 21 | 1 | Battery low temperature alarm(°C) | °C (roh−40) |
| 22 | 1 | Battery charging high temperature alarm | °C (roh−40) |
| 23 | 1 | Battery charging high temperature alarm recovery | °C (roh−40) |
| 24 | 1 | Battery discharge high temperature alarm | °C (roh−40) |
| 25 | 1 | Battery discharge high temperature alarm recovery | °C (roh−40) |
| 26 | 1 | Battery charging low temperature alarm | °C (roh−40) |
| 27 | 1 | Battery charging low temperature alarm recovery | °C (roh−40) |
| 28 | 1 | Battery discharge low temperature alarm | °C (roh−40) |
| 29 | 1 | Battery discharge low temperature alarm recovery | °C (roh−40) |
| 30 | 1 | Environment high temperature alarm | °C (roh−40) |
| 31 | 1 | Environment low temperature alarm | °C (roh−40) |
| 32 | 1 | Power high temperature alarm | °C (roh−40) |
| 33 | 1 | Power low temperature alarm | °C (roh−40) |
| 34 | 2 | Discharge current alarm | mA bzw. mS (roh×10) |
| 35 | 2 | Discharge current alarm recovery | mA bzw. mS (roh×10) |
| 36 | 2 | Charging current alarm | mA bzw. mS (roh×10) |
| 37 | 2 | Charging current alarm recovery | mA bzw. mS (roh×10) |
| 38 | 2 | Voltage difference alarm | mV (1:1) |
| 39 | 2 | Voltage difference alarm recovery(mV) | mV (1:1) |
| 40 | 2 | Voltage difference protection(mV) | mV (1:1) |
| 41 | 2 | Cell over voltage protection(mV) | mV (1:1) |
| 42 | 2 | Cell over voltage delay(mS) | mA bzw. mS (roh×10) |
| 43 | 2 | Cell over voltage recovery(mV) | mV (1:1) |
| 44 | 2 | Cell under voltage protection | mV (1:1) |
| 45 | 2 | Cell under voltage delay | mA bzw. mS (roh×10) |
| 46 | 2 | Cell under pressure recovery | mV (1:1) |
| 47 | 2 | Cell Shutdown voltage | mV (1:1) |
| 48 | 2 | Cell Shutdown time | 1:1 |
| 49 | 2 | Pack over voltage protection | mV (roh×2) |
| 50 | 2 | Pack overvoltage delay | mA bzw. mS (roh×10) |
| 51 | 2 | Pack over voltage recovery | mV (roh×2) |
| 52 | 2 | Pack under voltage protection | mV (roh×2) |
| 53 | 2 | Pack under pressure delay | mA bzw. mS (roh×10) |
| 54 | 2 | Pack under pressure recovery | mV (roh×2) |
| 55 | 1 | Charging high temperature protection | °C (roh−40) |
| 56 | 1 | Charging high temperature recovery | °C (roh−40) |
| 57 | 1 | Charging Low temperature protection | °C (roh−40) |
| 58 | 1 | Charging Low temperature recovery | °C (roh−40) |
| 59 | 1 | Discharge high temperature protection | °C (roh−40) |
| 60 | 1 | Discharge high temperature recovery | °C (roh−40) |
| 61 | 1 | Discharge low temperature protection | °C (roh−40) |
| 62 | 1 | Discharge low temperature recovery | °C (roh−40) |
| 63 | 1 | Environment high temperature protection | °C (roh−40) |
| 64 | 1 | Environmental high temperature recovery | °C (roh−40) |
| 65 | 1 | Environment low temperature protection | °C (roh−40) |
| 66 | 1 | Environment low temperature recovery | °C (roh−40) |
| 67 | 1 | Power high temperature protection | °C (roh−40) |
| 68 | 1 | Power high temperature recovery | °C (roh−40) |
| 69 | 1 | Power Low temperature protection | °C (roh−40) |
| 70 | 1 | Power low temperature recovery | °C (roh−40) |
| 71 | 2 | Charging over current protection | mA bzw. mS (roh×10) |
| 72 | 2 | Charging over current protection recovery | mA bzw. mS (roh×10) |
| 73 | 2 | Charge over current protect delay | mA bzw. mS (roh×10) |
| 74 | 2 | Charge over current protect recovery delay | mA bzw. mS (roh×10) |
| 75 | 2 | Number of charging over current automatic locking | Anzahl (1:1) |
| 76 | 2 | Effective lock time of charging over current | mA bzw. mS (roh×10) |
| 77 | 2 | Discharge over current protection 1 | mA bzw. mS (roh×10) |
| 78 | 2 | Discharge over current protection 1 recovery | mA bzw. mS (roh×10) |
| 79 | 2 | Discharge over current protection 1 delay | mA bzw. mS (roh×10) |
| 80 | 2 | Discharge over current protection11  recovery delay | mA bzw. mS (roh×10) |
| 81 | 2 | number of automatic Discharge over-current 1 | Anzahl (1:1) |
| 82 | 2 | Effective lock time of Discharging over current 1 | mA bzw. mS (roh×10) |
| 83 | 2 | Discharge over current protection 2 | mA bzw. mS (roh×10) |
| 84 | 2 | Discharge overcurrent protection delay 2 recovery | mA bzw. mS (roh×10) |
| 85 | 2 | Discharge over current recovery delay 2 | mA bzw. mS (roh×10) |
| 86 | 2 | number of automatic Discharge over-current 2 | Anzahl (1:1) |
| 87 | 2 | Effective lock time of Discharging over current 2 | mA bzw. mS (roh×10) |
| 88 | 1 | Short circuit protection | 1:1 |
| 89 | 2 | Precharge time | 1:1 |
| 90 | 2 | Short circuit protection delay | mA bzw. mS (roh×10) |
| 91 | 2 | Short circuit recovery time delay | mA bzw. mS (roh×10) |
| 92 | 2 | Short circuit Auto lock times | Anzahl (1:1) |
| 93 | 2 | Short circuit effective lock time | mA bzw. mS (roh×10) |
| 94 | 1 | Start temperature of heat sink | °C (roh−40) |
| 95 | 1 | End temperature of heat sink | °C (roh−40) |
| 96 | 1 | Heating start temperature | °C (roh−40) |
| 97 | 1 | Heating End temperature | °C (roh−40) |
| 98 | 2 | Auto Shutdown | 1:1 |
| 99 | 2 | Auto wake-up | 1:1 |
| 100 | 1 | RS485 Protocol type | 1:1 |
| 101 | 1 | CAN Protocol type | 1:1 |
| 102 | 2 | Functions contorl | 1:1 |
| 103 | 2 | Warning Disable | 1:1 |
| 104 | 2 | Protect Disable | 1:1 |
| 105 | 1 | None | 1:1 |


### Enum-/Bitfeld-Bedeutungen

- **Balance mode** (Idx 6): 0 = aus · 1 = Laden · 2 = Laden+Ruhe · 3 = Laden+Entladen+Ruhe
- **Short circuit protection** (Idx 88): Index in Liste 500A · 1000A · 1500A · 2000A · 2500A · 3000A · 3500A · 4000A · 4500A
- **RS485 Protocol type** (Idx 100): Liste u. a. Local, Growatt, Solax, Goodwe, Sofar, LuxPower, Victron, Pylontech, SMA …
- **CAN Protocol type** (Idx 101): analoge Liste
- **Functions control** (Idx 102, Bitmaske): enthält u. a. Heating, Current Limiting, Anti-thief
- **Warning/Protect Disable** (Idx 103/104, Bitmasken): einzelne Warnungen/Schutzfunktionen abschaltbar

> Die exakte Zahl→Text-Zuordnung der Protokoll-Dropdowns und die Bit-Belegung von „Functions control / Warning Disable / Protect Disable" lassen sich mit je einem „Umstellen + Mitschnitt" final fixieren.

---

## 6. Records (History `CID2 = 4B` / Warning `CID2 = 4C`)

**Seitenweises Lesen** über das erste INFO-Byte:

- `00` = ersten/aktuellsten Datensatz lesen
- `01` = nächsten Datensatz lesen (wiederholen)
- `03` = Ende (kurze Antwort, keine Daten)

Pro Datensatz 59 Byte. Verifizierte Felder:

| Feld | Kodierung |
|---|---|
| Modus | 1 B (z. B. Laden/Entladen/Ruhe) |
| Zeitstempel | YY MM DD HH MM SS (Jahr = 2000 + YY) |
| Strom | `s16` × 10 mA |
| Gesamtspannung | `u16` × 10 mV |
| SOC | `u8` % |
| Restkapazität | `u16` × 10 mAh |
| (Warning) Alarm-Code | 1 B (z. B. `0x31` = 49) |
| Temperaturen | 6 × (`u16` − 40 °C) |
| Zellspannungen | 16 × `u16` mV |

*Verifiziert* gegen GUI: Spannung 53,29 V, Strom 4,50 A, SOC 99 %, Restkap. 149,16 Ah; Alarm-Code 49 im ersten Warning-Record.

---

## 7. System-Konfiguration

### 7.1 Echtzeituhr — lesen `4D`, schreiben `4E`
Format: `JJJJ MM DD HH MM SS` (Jahr 2 Byte, z. B. `0x07EA` = 2026). Achtung: Records nutzen 1-Byte-Jahr, RTC nutzt 2-Byte-Jahr.

### 7.2 Kapazität — lesen `E5`
INFO: Pack + Designed (`u16`) + Full (`u16`) + Remain (`u16`), je × 10 mAh. (150,00 Ah = `0x3A98`.)

### 7.3 Total Discharge — lesen `EB`
INFO: Pack + Total (`u32`, Ah) + Energie (`u32`, kWh). Verifiziert: 408165 Ah / 91855 kWh.

### 7.4 Kalibrierung — lesen `ED`
INFO-Anfrage: Pack + Gruppe + Item.
- Gruppe `02` = Stromkalibrierung, Items `15` = Zero, `16` = Charge, `17` = Discharge (`u32`-Werte).
- Gruppe `03` = Temperaturkalibrierung, Item = Sensornummer (`u16`, 0,1 °C → 300 = 30,0 °C).

### 7.5 Firmware-/Versionsstring — lesen `E9`
ASCII-String, z. B. `VK51100_DGGZ_V15.53`.

### 7.6 Produktinformationen — lesen `F1`, schreiben `F2`
7 Felder à **30 ASCII-Zeichen** (rechts mit Leerzeichen aufgefüllt), in Reihenfolge:
Company Name · Manufacture · Pack Name · Pack Version · Battery Type · Pack Serial Number · Customer INFO.

---

## 8. Steuerung

### 8.1 MOS / Strombegrenzung — schreiben `E2`
INFO: Pack (1 B) + **2-Byte-Zustandsmaske**. Die Maske gibt **den kompletten Soll-Zustand** an (nicht einen einzelnen Tastendruck):

| Bit | Hex | Funktion |
|---|---|---|
| 6 | `0x40` | CFET (Lade-MOS) offen |
| 5 | `0x20` | DFET (Entlade-MOS) offen |
| 7 | `0x80` | Current Limiting aktiv |

Beispiele: `0x00` = alles gesperrt · `0x40` = nur Laden · `0x60` = Laden+Entladen · `0xE0` = alles offen + Strombegrenzung.

### 8.2 Power Off — schreiben `EF`
INFO: Pack. Versetzt das BMS in den Abschalt-/Schlafzustand (öffnet die MOSFETs, Ausgang spannungslos). **Reversibel** (Aufwecken üblicherweise durch Anlegen einer Ladespannung). Laufende Kommunikation/Last kann den Übergang verhindern.

---

## 9. Hinweise für die eigene Implementierung

- **Eine serielle Verbindung genügt.** Nur die real vorhandenen Pack-Adressen direkt abfragen (z. B. 1 und 2), mit kurzem Timeout. Ein voller Zyklus (2 Packs × {42, 44}) dauert ~1 s. Die Original-App war nur langsam, weil sie blind alle Adressen 1–8 mit langem Timeout durchläuft.
- **TCP-Direktanbindung möglich:** Das RS232-Ethernet-Gateway stellt die Ports als TCP-Server bereit — der VCOM-Treiber kann entfallen.
- **Antworten kommen fragmentiert** über mehrere TCP-Pakete; bis zum `CR` (`0x0D`) zusammensetzen, dann Prüfsumme verifizieren.
- **Robustheit:** pro Abfrage `try/except`, fehlende Antwort tolerieren, kein Absturz der Schleife. (Die Original-GUI bricht gelegentlich ab — eine eigene, sauber gekapselte Schleife ist stabiler.)
- **GUI-Passwort `vking`** ist nur eine lokale Sperre der Hersteller-Oberfläche und wird **nicht** über die Leitung gesendet. Ein eigenes Tool benötigt es nicht.
- **Schreiben mit Bedacht:** Parameter- und Kalibrierungswerte sollten nach dem Schreiben sofort zurückgelesen und verglichen werden. Falsche Schutzparameter können Sicherheitsfunktionen verstellen.
