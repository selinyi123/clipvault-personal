"""Report release-sensitive optional Pillow features from the active interpreter."""

from PIL import features


for feature_name in ("libimagequant", "raqm"):
    print(f"{feature_name}={features.check_feature(feature_name)}")
