from pikorua_adflow.crews.content_crew.task_composer import list_variants, get_variant_meta
from pikorua_adflow.api.models import ImageGenReq

variants = list_variants()
print("Variants:", variants)
for vk in variants:
    meta = get_variant_meta(vk)
    opt_in = meta.get("opt_in", False)
    palettes = meta["allowed_palettes"]
    print(f"  {vk}: opt_in={opt_in}, palettes={palettes}")

req = ImageGenReq(exterior_brief="Two towers, glass facade")
print("ImageGenReq exterior_brief:", req.exterior_brief)
print("OK")
