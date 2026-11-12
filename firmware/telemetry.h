#ifndef TELEMETRY_H_
#define TELEMETRY_H_

#include "contiki.h"
#include <stdint.h>

/* Use a dedicated telemetry port beside Contiki's automatic CoAP engine. */
#define WSN_PORT 5685
#define WSN_PATH "telemetry"
#define WSN_PAYLOAD_LEN 13
#define WSN_COAP_MAX 64

struct reading {
  uint16_t node;
  uint32_t seq;
  uint32_t local_seconds;
  uint16_t temp_centi;
  uint16_t humidity_centi;
};

void reading_encode(uint8_t *bytes, const struct reading *reading);
void reading_decode(struct reading *reading, const uint8_t *bytes);
PROCESS_NAME(energy_reporter);

#endif
