/**
  ******************************************************************************
  * @file    uf4com_usrt.h
  * @author  UF4
  * @date    26-6-13 下午1:42
  * @brief
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 UF4.
  * All rights reserved.
  *
  * This software is provided "as is", without warranty of any kind.
  *
  ******************************************************************************
  */
#ifndef UF4COM_ID_TABLE_H
#define UF4COM_ID_TABLE_H

#include <stddef.h>
#include <stdint.h>

#include "uf4com.h"

#define UF4_ID_PERMISSION_RO 0u
#define UF4_ID_PERMISSION_RW 1u

struct uf4_id_s;
typedef uf4_error_t (*uf4_id_read_fn_t)(const struct uf4_id_s *item,
                                        uint16_t *value);
typedef uf4_error_t (*uf4_id_validate_write_fn_t)(const struct uf4_id_s *item,
                                                  uint16_t value);
typedef uf4_error_t (*uf4_id_write_fn_t)(const struct uf4_id_s *item,
                                         uint16_t value);

typedef struct uf4_id_s
{
  uint8_t id;
  uint16_t *ptr;
  uint8_t rw;
  uf4_id_read_fn_t read;
  uf4_id_validate_write_fn_t validate_write;
  uf4_id_write_fn_t write;
  void *context;
} uf4_id_t;

void UF4_IDTableBind(uf4_id_t *table, size_t count);
uf4_id_t *UF4_FindID(uint8_t id);
size_t UF4_IDTableCount(void);
size_t UF4_GetAllIDs(uint8_t *out_ids, size_t max_count);
uf4_error_t UF4_IDRead(const uf4_id_t *item, uint16_t *value);
uf4_error_t UF4_IDValidateWrite(const uf4_id_t *item, uint16_t value);
uf4_error_t UF4_IDWrite(const uf4_id_t *item, uint16_t value);

#endif // UF4COM_ID_TABLE_H
