/**
  ******************************************************************************
  * @file    uf4com_id_table.c
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
#include "uf4com_id_table.h"

static uf4_id_t *s_table;
static size_t s_table_count;

void UF4_IDTableBind(uf4_id_t *table, size_t count)
{
  s_table = table;
  s_table_count = count;
}

uf4_id_t *UF4_FindID(uint8_t id)
{
  for(size_t i = 0u; i < s_table_count; i++)
  {
    if(s_table[i].id == id)
    {
      return &s_table[i];
    }
  }

  return NULL;
}

size_t UF4_IDTableCount(void)
{
  return s_table_count;
}

size_t UF4_GetAllIDs(uint8_t *out_ids, size_t max_count)
{
  size_t i;
  size_t copy_count = (s_table_count < max_count) ? s_table_count : max_count;

  if(out_ids == NULL)
  {
    return 0u;
  }

  for(i = 0u; i < copy_count; i++)
  {
    out_ids[i] = s_table[i].id;
  }

  return copy_count;
}

uf4_error_t UF4_IDRead(const uf4_id_t *item, uint16_t *value)
{
  if(item == NULL || value == NULL)
  {
    return UF4_ERR_INTERNAL;
  }
  if(item->read != NULL)
  {
    return item->read(item, value);
  }
  if(item->ptr == NULL)
  {
    return UF4_ERR_INTERNAL;
  }
  *value = *(item->ptr);
  return UF4_OK;
}

uf4_error_t UF4_IDValidateWrite(const uf4_id_t *item, uint16_t value)
{
  if(item == NULL)
  {
    return UF4_ERR_INVALID_ID;
  }
  if(item->rw != UF4_ID_PERMISSION_RW)
  {
    return UF4_ERR_WRITE_PROTECT;
  }
  if(item->validate_write != NULL)
  {
    return item->validate_write(item, value);
  }
  return (item->ptr != NULL || item->write != NULL) ? UF4_OK : UF4_ERR_INTERNAL;
}

uf4_error_t UF4_IDWrite(const uf4_id_t *item, uint16_t value)
{
  uf4_error_t status = UF4_IDValidateWrite(item, value);
  if(status != UF4_OK)
  {
    return status;
  }
  if(item->write != NULL)
  {
    return item->write(item, value);
  }
  *(item->ptr) = value;
  return UF4_OK;
}
