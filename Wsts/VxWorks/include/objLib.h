/*
 * objLib.h: the VxWorks API shim of fprime-wsts. Error codes of kernel objects.
 */
#ifndef WSTS_VXWORKS_OBJLIB_H
#define WSTS_VXWORKS_OBJLIB_H

#include <vxWorks.h>

#define S_objLib_OBJ_ID_ERROR (M_objLib | 1)
#define S_objLib_OBJ_UNAVAILABLE (M_objLib | 2)
#define S_objLib_OBJ_DELETED (M_objLib | 3)
#define S_objLib_OBJ_TIMEOUT (M_objLib | 4)
#define S_objLib_OBJ_NO_METHOD (M_objLib | 5)

#endif
