"""Reusable ComfyUI boundary nodes for comfyui-image-frontend."""

from .nodes import (
    CIFBooleanParameter,
    CIFChoiceParameter,
    CIFDecimalParameter,
    CIFImageFrontendInterface,
    CIFImageParameter,
    CIFIntegerParameter,
    CIFLoraStack,
    CIFPublishImage,
    CIFPublishText,
    CIFSeedParameter,
    CIFTextParameter,
)

NODE_CLASS_MAPPINGS = {
    "CIFImageFrontendInterface": CIFImageFrontendInterface,
    "CIFLoraStack": CIFLoraStack,
    "CIFTextParameter": CIFTextParameter,
    "CIFIntegerParameter": CIFIntegerParameter,
    "CIFDecimalParameter": CIFDecimalParameter,
    "CIFBooleanParameter": CIFBooleanParameter,
    "CIFChoiceParameter": CIFChoiceParameter,
    "CIFImageParameter": CIFImageParameter,
    "CIFSeedParameter": CIFSeedParameter,
    "CIFPublishImage": CIFPublishImage,
    "CIFPublishText": CIFPublishText,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CIFImageFrontendInterface": "Image Frontend Interface",
    "CIFLoraStack": "Image Frontend LoRA Stack",
    "CIFTextParameter": "Image Frontend Text Parameter",
    "CIFIntegerParameter": "Image Frontend Integer Parameter",
    "CIFDecimalParameter": "Image Frontend Decimal Parameter",
    "CIFBooleanParameter": "Image Frontend Boolean Parameter",
    "CIFChoiceParameter": "Image Frontend Choice Parameter",
    "CIFImageParameter": "Image Frontend Image Parameter",
    "CIFSeedParameter": "Image Frontend Seed Parameter",
    "CIFPublishImage": "Publish Image to Image Frontend",
    "CIFPublishText": "Publish Text to Image Frontend",
}

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
