// The published catalog exactly as it exists on the appliance, read from the live
// ComfyUI userdata manifests on 2026-10-31, so this preview shows the real
// workflow names, checkpoints, architectures and pipeline inventories.

export const publishedCatalog = [
  {
  "source_key": "a4406621bf791bea946929eae421b19762f7a2d29399156bc74ff643ec8c12e5",
  "display_name": "Moody Krea2 i2i 4KHD v1",
  "available": true,
  "readiness": "ready",
  "cached": true,
  "revision": {
    "publication_id": "77f7a75f-33b3-447a-bd78-715e19e49968"
  },
  "generation_source": {
    "generation_type": "image_to_image",
    "dimension_policy": "source_image",
    "summary": "Transforms a required reference image according to prompt guidance using source-image-derived dimensions and returns a declared final result.",
    "tags": [
      "color_matching",
      "detailer",
      "image_blending",
      "image_sharpening",
      "image_to_image",
      "krea2",
      "model_upscale",
      "prompt_guided",
      "seedvr2",
      "ultimate_sd_upscale",
      "ultralytics"
    ],
    "base_model": {
      "family": "krea2",
      "family_label": "Krea 2",
      "architecture": "krea2",
      "architecture_label": "Krea 2"
    }
  },
  "technical_inventory": {
    "node_counts": {
      "editable_root": 127,
      "subgraph_definitions": 0,
      "editable_subgraph_nodes": 0,
      "compiled_api": 90,
      "output_reachable": 85,
      "compiled_orphans": 5
    },
    "upscalers": [
      "4x-ClearRealityV1.pth",
      "seedvr2_ema_7b-Q4_K_M.gguf"
    ],
    "samplers": 1
  },
  "model_selectors": [
    {
      "parameter_id": "checkpoint",
      "label": "Checkpoint",
      "description": "Base checkpoint (UNETLoader binding). Option ids are stable; bindings point at installed NVFP4/FP8 quantizations under the shared model library.",
      "default": "moody_amateur_mix_v4",
      "choices": [
      {"value": "moody_krea2_mix_v70", "label": "Moody Krea 2 Mix V8.0 (NVFP4)", "checkpoint_id": "cp1_a90aeadb16481494e7ae43f8e8ba9c479d96f33282194e36facd90f6c619991e"},
      {"value": "moody_krea2_mix_v7se", "label": "Moody Krea 2 Mix V7SE 反差萌妹TYJR Vol2 (NVFP4)", "checkpoint_id": "cp1_51d6df323642f42e2e7f320e3cd6cb7a74c940107a24ccf2ad43fa51750e2086"},
      {"value": "moody_amateur_mix_v4", "label": "Moody Amateur Mix V5 Prosumer Shots (NVFP4)", "checkpoint_id": "cp1_765c6e13120ca77445f78c271fa7294f82dbba35a6daab490f502f7d01028c6f"},
      {"value": "gonzalomo_krea2_v40_fp8", "label": "Gonzalomo Krea 2 v40 (FP8)", "checkpoint_id": "cp1_639081bf28305bfc3fb16a088fc40719604aa0bc57eac0141a10802aaec1c1f3"},
      {"value": "dasiwa_krea2_turbo_cutedisaster_v2_fp8", "label": "Dasiwa Krea 2 Turbo Raw – Dark Desire V3 UC (FP8)", "checkpoint_id": "cp1_b81b317f3a3fa1475d7d534cb4a0594dcc7a216bcd8f8ab0e1efb22e9641a9ad"},
      {"value": "realism_by_stable_yogi_v25_nvfp4", "label": "Realism by Stable Yogi V2.5 (NVFP4)", "checkpoint_id": "cp1_7f90215e0c03cfb9c6213dc9fd83d91be7783d7a8bf83a1cf414baedc265a585"},
      {"value": "realism_by_stable_yogi_v30_nvfp4", "label": "Realism by Stable Yogi V3.0 Extended (NVFP4)", "checkpoint_id": "cp1_07ea5edc3f144e193e982fd50e56fcb6cac9b81a28c382f20e5235ba7bbe3add"},
      {"value": "selfora_v21_night_fix_fp8", "label": "Selfora v2.1 Night Fix (FP8)", "checkpoint_id": "cp1_721459142aa39c6ced072557205538f3705ce2b069d5a68ac50915a9b9ea90c1"},
      {"value": "muse_by_stable_yogi_v35_nvfp4", "label": "Muse by Stable Yogi V3.5 (NVFP4)", "checkpoint_id": "cp1_b2e08281f41fbb7ed5b735a01e2426ddf26fbf9a9461587bddb61673f0a8c2d1"},
      {"value": "sat_dirtyrealism_krea2_v5_fp8", "label": "SAT DirtyRealism Krea 2 – Dirty Uncut (FP8)", "checkpoint_id": "cp1_d0a936b10d49b978a2a7485a51c505378493a733379c5d8362bdb06f62f9ca8c"},
      {"value": "intorealism_krea2_edit_v10_int8", "label": "IntoRealism Krea 2 V5.0 (INT8)", "checkpoint_id": "cp1_7b2fba79b124adc5abcaf2fb7a6ab93bc14e563cfe882a7c3f83d41b06788d63"},
      {"value": "fascium_krea2_nsfwmerge4_fp8", "label": "FASCIUM Krea 2 Realistic Merge NSFW (FP8)", "checkpoint_id": "cp1_49df8aa87089ad0d21ae897cba2e2d1b838215b920c07cd5bdbbb6a5ff87c0e5"},
      {"value": "krea2_finalcut_update_fp8", "label": "Krea2 FinalCut Update (FP8)", "checkpoint_id": "cp1_e88cf4f82b5f1440b39d18b067d61a4def7ffa2867ead605d1125494f6c8504f"},
      {"value": "asian_utopian_krea2_v20_nvfp4", "label": "1125 Asian Utopian Krea 2 V2.0 (NVFP4)", "checkpoint_id": "cp1_89a932608d24f46a0737c10bd19c41ddfa524a65ca0e0585b3469c529b45605a"},
      {"value": "sat_ior_krea2_v2_fp8", "label": "SAT IOR Imitation of Reality V3 (FP8)", "checkpoint_id": "cp1_9a2149273d222d4b446ec762d2626d440895b4222648cfcaa43b1b0288e8f07a"},
      {"value": "sinox_krea2_edit_v11_fp8", "label": "SinoX Krea 2 Aesthetics Vision V1.1 (FP8)", "checkpoint_id": "cp1_5ee73c7e59c5006f7730bb959a9d539c2e2d9be7b6c8edd49bc016c97d53e3ac"},
      {"value": "dark_beast_3_krea2", "label": "Dark Beast 3 黑兽 3.0 (FP8)", "checkpoint_id": "cp1_f404d6b90594c8edd62135e1504872bb6af5085148b5903468a09568b2cb63e6"},
      {"value": "haruki_mix_kr2_v20", "label": "HARUKI MIX KR2 V2.0 (INT8 ConvRot)", "checkpoint_id": "cp1_fc4f7ebb33e554ddedc65f316e64c41ec521885243a39f29008945f3a3193b0c"},
      {"value": "redgpt2_krea2_turbo_gpt", "label": "REDGPT2 Krea 2 Turbo GPT逼真版 (FP8)", "checkpoint_id": "cp1_8a6ba80e526a7034bff04e772ca051a9b2599c665acfd81b6ef71e95d3657697"},
      {"value": "unstable_bastard_krea2", "label": "UnstableBastard BastardKrea2 (INT8)", "checkpoint_id": "cp1_b7a8577a4c28a014d9236918289c9c1148da60f71997471a63c740a5eb1c0cd9"},
      {"value": "ray_artshoot_krea2_v4", "label": "Ray Artshoot Krea 2 NSFW V4 (INT8)", "checkpoint_id": "cp1_366eec952a00fbb8ef3f857a14a9c227be87e0d3fdd4b176430d4e300b51a6d9"},
      {"value": "pornmaster_krea2_turbo_v2", "label": "PornMaster 色情大師 Krea 2 Turbo V2 (FP8)", "checkpoint_id": "cp1_b1a128f284fa0983c7cc67fb43db681be66c81b7fe45dd1aeb770ac1f59ca62c"},
      {"value": "fineporn_v4", "label": "FinePorn V4 (FP8)", "checkpoint_id": "cp1_b61c89eaed05e25eab86d84be52822f27801f394666aabdec263ef637f6df889"},
      {"value": "analog_madness_krea2_v30", "label": "Analog Madness Krea 2 Turbo V3.0 (FP8)", "checkpoint_id": "cp1_dd8953b905f14397798ee33b72eea5455132bb60f36024aea5f263e452a83bfa"},
      {"value": "cielbleu_krea2_v1", "label": "CielBleu Krea 2 V1 (FP8)", "checkpoint_id": "cp1_b6200595e4a991f5ee63e90055ad32b0ae6c65a149d46b0c00ed884cbe5a83d6"},
      {"value": "winnougan_10_toes_v13", "label": "Winnougan 10 Toes V1.3 (FP8)", "checkpoint_id": "cp1_84aca28923f5daa05d861185c65f604061cf626ed6d848aa266bef3bd111b172"},
      {"value": "lox_utopic_world_v20", "label": "Lox's Utopic World V2.0 Quants (FP8)", "checkpoint_id": "cp1_aaef3baf9b9a790cbd64f622a82f34b09f5a381cc609877db22b40944e55283f"},
      {"value": "jib_mix_krea_v10", "label": "Jib Mix Krea 2 V4.0 Habanero (INT8)", "checkpoint_id": "cp1_cbf26549abe22b6bde6c49cbbf2cbba5ff69851b4d3cc2acfc57953d6c7c8d0d"},
      {"value": "badmilk_melancholy_v10", "label": "Badmilkmelancholy Krea 2 Turbo V1.0 (FP8)", "checkpoint_id": "cp1_58899dc863451a1b8ab309a4949a7da50d917ecf9116fa075c7b0820891edfbd"},
      {"value": "unstable_dissolution_krea2", "label": "unStable Dissolution Krea 2 Turbo (FP8)", "checkpoint_id": "cp1_963b10a40613e7e1b116e87a438b0c7bfd2f6ec20de26d02c12b21cb4f6cea02"}
    ]
    }
  ]
},
  {
  "source_key": "3429cd1442ccde4f520c45940968133de38c020dba73b9ca87ea94ed8332025e",
  "display_name": "Moody Krea2 Advanced v1",
  "available": true,
  "readiness": "ready",
  "cached": true,
  "revision": {
    "publication_id": "69f322d4-eee1-4037-8db6-579d72aa88d8"
  },
  "generation_source": {
    "generation_type": "text_to_image",
    "dimension_policy": "explicit",
    "summary": "Generates images from prompt guidance using explicit dimensions and returns a declared final result.",
    "tags": [
      "color_matching",
      "detailer",
      "image_blending",
      "krea2",
      "prompt_guided",
      "text_to_image",
      "ultimate_sd_upscale",
      "ultralytics"
    ],
    "base_model": {
      "family": "krea2",
      "family_label": "Krea 2",
      "architecture": "krea2",
      "architecture_label": "Krea 2"
    }
  },
  "technical_inventory": {
    "node_counts": {
      "editable_root": 128,
      "subgraph_definitions": 0,
      "editable_subgraph_nodes": 0,
      "compiled_api": 77,
      "output_reachable": 60,
      "compiled_orphans": 17
    },
    "upscalers": [
      "4x-ClearRealityV1.pth"
    ],
    "samplers": 3
  },
  "model_selectors": [
    {
      "parameter_id": "checkpoint",
      "label": "Checkpoint",
      "description": "Base checkpoint for the Krea2 pipeline. V7.0 is the Moody Krea 2 Mix; V7SE is the fan-art remix variant. NVFP4.",
      "default": "moody_krea2_mix_v70",
      "choices": [
      {"value": "moody_krea2_mix_v70", "label": "Moody Krea 2 Mix V8.0 (NVFP4)", "checkpoint_id": "cp1_a90aeadb16481494e7ae43f8e8ba9c479d96f33282194e36facd90f6c619991e"},
      {"value": "moody_krea2_mix_v7se", "label": "Moody Krea 2 Mix V7SE 反差萌妹TYJR Vol2 (NVFP4)", "checkpoint_id": "cp1_51d6df323642f42e2e7f320e3cd6cb7a74c940107a24ccf2ad43fa51750e2086"},
      {"value": "moody_amateur_mix_v4", "label": "Moody Amateur Mix V5 Prosumer Shots (NVFP4)", "checkpoint_id": "cp1_765c6e13120ca77445f78c271fa7294f82dbba35a6daab490f502f7d01028c6f"},
      {"value": "gonzalomo_krea2_v40_fp8", "label": "Gonzalomo Krea 2 v40 (FP8)", "checkpoint_id": "cp1_639081bf28305bfc3fb16a088fc40719604aa0bc57eac0141a10802aaec1c1f3"},
      {"value": "dasiwa_krea2_turbo_cutedisaster_v2_fp8", "label": "Dasiwa Krea 2 Turbo Raw – Dark Desire V3 UC (FP8)", "checkpoint_id": "cp1_b81b317f3a3fa1475d7d534cb4a0594dcc7a216bcd8f8ab0e1efb22e9641a9ad"},
      {"value": "realism_by_stable_yogi_v25_nvfp4", "label": "Realism by Stable Yogi V2.5 (NVFP4)", "checkpoint_id": "cp1_7f90215e0c03cfb9c6213dc9fd83d91be7783d7a8bf83a1cf414baedc265a585"},
      {"value": "realism_by_stable_yogi_v30_nvfp4", "label": "Realism by Stable Yogi V3.0 Extended (NVFP4)", "checkpoint_id": "cp1_07ea5edc3f144e193e982fd50e56fcb6cac9b81a28c382f20e5235ba7bbe3add"},
      {"value": "selfora_v21_night_fix_fp8", "label": "Selfora v2.1 Night Fix (FP8)", "checkpoint_id": "cp1_721459142aa39c6ced072557205538f3705ce2b069d5a68ac50915a9b9ea90c1"},
      {"value": "muse_by_stable_yogi_v35_nvfp4", "label": "Muse by Stable Yogi V3.5 (NVFP4)", "checkpoint_id": "cp1_b2e08281f41fbb7ed5b735a01e2426ddf26fbf9a9461587bddb61673f0a8c2d1"},
      {"value": "sat_dirtyrealism_krea2_v5_fp8", "label": "SAT DirtyRealism Krea 2 – Dirty Uncut (FP8)", "checkpoint_id": "cp1_d0a936b10d49b978a2a7485a51c505378493a733379c5d8362bdb06f62f9ca8c"},
      {"value": "intorealism_krea2_edit_v10_int8", "label": "IntoRealism Krea 2 V5.0 (INT8)", "checkpoint_id": "cp1_7b2fba79b124adc5abcaf2fb7a6ab93bc14e563cfe882a7c3f83d41b06788d63"},
      {"value": "fascium_krea2_nsfwmerge4_fp8", "label": "FASCIUM Krea 2 Realistic Merge NSFW (FP8)", "checkpoint_id": "cp1_49df8aa87089ad0d21ae897cba2e2d1b838215b920c07cd5bdbbb6a5ff87c0e5"},
      {"value": "krea2_finalcut_update_fp8", "label": "Krea2 FinalCut Update (FP8)", "checkpoint_id": "cp1_e88cf4f82b5f1440b39d18b067d61a4def7ffa2867ead605d1125494f6c8504f"},
      {"value": "asian_utopian_krea2_v20_nvfp4", "label": "1125 Asian Utopian Krea 2 V2.0 (NVFP4)", "checkpoint_id": "cp1_89a932608d24f46a0737c10bd19c41ddfa524a65ca0e0585b3469c529b45605a"},
      {"value": "sat_ior_krea2_v2_fp8", "label": "SAT IOR Imitation of Reality V3 (FP8)", "checkpoint_id": "cp1_9a2149273d222d4b446ec762d2626d440895b4222648cfcaa43b1b0288e8f07a"},
      {"value": "sinox_krea2_edit_v11_fp8", "label": "SinoX Krea 2 Aesthetics Vision V1.1 (FP8)", "checkpoint_id": "cp1_5ee73c7e59c5006f7730bb959a9d539c2e2d9be7b6c8edd49bc016c97d53e3ac"},
      {"value": "dark_beast_3_krea2", "label": "Dark Beast 3 黑兽 3.0 (FP8)", "checkpoint_id": "cp1_f404d6b90594c8edd62135e1504872bb6af5085148b5903468a09568b2cb63e6"},
      {"value": "haruki_mix_kr2_v20", "label": "HARUKI MIX KR2 V2.0 (INT8 ConvRot)", "checkpoint_id": "cp1_fc4f7ebb33e554ddedc65f316e64c41ec521885243a39f29008945f3a3193b0c"},
      {"value": "redgpt2_krea2_turbo_gpt", "label": "REDGPT2 Krea 2 Turbo GPT逼真版 (FP8)", "checkpoint_id": "cp1_8a6ba80e526a7034bff04e772ca051a9b2599c665acfd81b6ef71e95d3657697"},
      {"value": "unstable_bastard_krea2", "label": "UnstableBastard BastardKrea2 (INT8)", "checkpoint_id": "cp1_b7a8577a4c28a014d9236918289c9c1148da60f71997471a63c740a5eb1c0cd9"},
      {"value": "ray_artshoot_krea2_v4", "label": "Ray Artshoot Krea 2 NSFW V4 (INT8)", "checkpoint_id": "cp1_366eec952a00fbb8ef3f857a14a9c227be87e0d3fdd4b176430d4e300b51a6d9"},
      {"value": "pornmaster_krea2_turbo_v2", "label": "PornMaster 色情大師 Krea 2 Turbo V2 (FP8)", "checkpoint_id": "cp1_b1a128f284fa0983c7cc67fb43db681be66c81b7fe45dd1aeb770ac1f59ca62c"},
      {"value": "fineporn_v4", "label": "FinePorn V4 (FP8)", "checkpoint_id": "cp1_b61c89eaed05e25eab86d84be52822f27801f394666aabdec263ef637f6df889"},
      {"value": "analog_madness_krea2_v30", "label": "Analog Madness Krea 2 Turbo V3.0 (FP8)", "checkpoint_id": "cp1_dd8953b905f14397798ee33b72eea5455132bb60f36024aea5f263e452a83bfa"},
      {"value": "cielbleu_krea2_v1", "label": "CielBleu Krea 2 V1 (FP8)", "checkpoint_id": "cp1_b6200595e4a991f5ee63e90055ad32b0ae6c65a149d46b0c00ed884cbe5a83d6"},
      {"value": "winnougan_10_toes_v13", "label": "Winnougan 10 Toes V1.3 (FP8)", "checkpoint_id": "cp1_84aca28923f5daa05d861185c65f604061cf626ed6d848aa266bef3bd111b172"},
      {"value": "lox_utopic_world_v20", "label": "Lox's Utopic World V2.0 Quants (FP8)", "checkpoint_id": "cp1_aaef3baf9b9a790cbd64f622a82f34b09f5a381cc609877db22b40944e55283f"},
      {"value": "jib_mix_krea_v10", "label": "Jib Mix Krea 2 V4.0 Habanero (INT8)", "checkpoint_id": "cp1_cbf26549abe22b6bde6c49cbbf2cbba5ff69851b4d3cc2acfc57953d6c7c8d0d"},
      {"value": "badmilk_melancholy_v10", "label": "Badmilkmelancholy Krea 2 Turbo V1.0 (FP8)", "checkpoint_id": "cp1_58899dc863451a1b8ab309a4949a7da50d917ecf9116fa075c7b0820891edfbd"},
      {"value": "unstable_dissolution_krea2", "label": "unStable Dissolution Krea 2 Turbo (FP8)", "checkpoint_id": "cp1_963b10a40613e7e1b116e87a438b0c7bfd2f6ec20de26d02c12b21cb4f6cea02"}
    ]
    }
  ]
},
  {
  "source_key": "0cc3f9ef4663ba5aa8996c26593eb8fbd67fd19dabd3a016c6b773e30ffc683e",
  "display_name": "Moody Krea2 Primer v1",
  "available": true,
  "readiness": "ready",
  "cached": true,
  "revision": {
    "publication_id": "ceead30b-3a6e-4c24-b282-de9f6e19f641"
  },
  "generation_source": {
    "generation_type": "text_to_image",
    "dimension_policy": "explicit",
    "summary": "Generates images from prompt guidance using explicit dimensions and returns a declared final result.",
    "tags": [
      "color_matching",
      "detailer",
      "image_blending",
      "krea2",
      "prompt_guided",
      "text_to_image",
      "ultimate_sd_upscale",
      "ultralytics"
    ],
    "base_model": {
      "family": "krea2",
      "family_label": "Krea 2",
      "architecture": "krea2",
      "architecture_label": "Krea 2"
    }
  },
  "technical_inventory": {
    "node_counts": {
      "editable_root": 123,
      "subgraph_definitions": 0,
      "editable_subgraph_nodes": 0,
      "compiled_api": 72,
      "output_reachable": 65,
      "compiled_orphans": 7
    },
    "upscalers": [
      "4x-ClearRealityV1.pth"
    ],
    "samplers": 3
  },
  "model_selectors": [
    {
      "parameter_id": "checkpoint",
      "label": "Checkpoint",
      "description": "Base checkpoint (UNETLoader binding). Option ids are stable; bindings point at installed NVFP4/FP8 quantizations under the shared model library.",
      "default": "moody_krea2_mix_v70",
      "choices": [
      {"value": "moody_krea2_mix_v70", "label": "Moody Krea 2 Mix V8.0 (NVFP4)", "checkpoint_id": "cp1_a90aeadb16481494e7ae43f8e8ba9c479d96f33282194e36facd90f6c619991e"},
      {"value": "moody_krea2_mix_v7se", "label": "Moody Krea 2 Mix V7SE 反差萌妹TYJR Vol2 (NVFP4)", "checkpoint_id": "cp1_51d6df323642f42e2e7f320e3cd6cb7a74c940107a24ccf2ad43fa51750e2086"},
      {"value": "moody_amateur_mix_v4", "label": "Moody Amateur Mix V5 Prosumer Shots (NVFP4)", "checkpoint_id": "cp1_765c6e13120ca77445f78c271fa7294f82dbba35a6daab490f502f7d01028c6f"},
      {"value": "gonzalomo_krea2_v40_fp8", "label": "Gonzalomo Krea 2 v40 (FP8)", "checkpoint_id": "cp1_639081bf28305bfc3fb16a088fc40719604aa0bc57eac0141a10802aaec1c1f3"},
      {"value": "dasiwa_krea2_turbo_cutedisaster_v2_fp8", "label": "Dasiwa Krea 2 Turbo Raw – Dark Desire V3 UC (FP8)", "checkpoint_id": "cp1_b81b317f3a3fa1475d7d534cb4a0594dcc7a216bcd8f8ab0e1efb22e9641a9ad"},
      {"value": "realism_by_stable_yogi_v25_nvfp4", "label": "Realism by Stable Yogi V2.5 (NVFP4)", "checkpoint_id": "cp1_7f90215e0c03cfb9c6213dc9fd83d91be7783d7a8bf83a1cf414baedc265a585"},
      {"value": "realism_by_stable_yogi_v30_nvfp4", "label": "Realism by Stable Yogi V3.0 Extended (NVFP4)", "checkpoint_id": "cp1_07ea5edc3f144e193e982fd50e56fcb6cac9b81a28c382f20e5235ba7bbe3add"},
      {"value": "selfora_v21_night_fix_fp8", "label": "Selfora v2.1 Night Fix (FP8)", "checkpoint_id": "cp1_721459142aa39c6ced072557205538f3705ce2b069d5a68ac50915a9b9ea90c1"},
      {"value": "muse_by_stable_yogi_v35_nvfp4", "label": "Muse by Stable Yogi V3.5 (NVFP4)", "checkpoint_id": "cp1_b2e08281f41fbb7ed5b735a01e2426ddf26fbf9a9461587bddb61673f0a8c2d1"},
      {"value": "sat_dirtyrealism_krea2_v5_fp8", "label": "SAT DirtyRealism Krea 2 – Dirty Uncut (FP8)", "checkpoint_id": "cp1_d0a936b10d49b978a2a7485a51c505378493a733379c5d8362bdb06f62f9ca8c"},
      {"value": "intorealism_krea2_edit_v10_int8", "label": "IntoRealism Krea 2 V5.0 (INT8)", "checkpoint_id": "cp1_7b2fba79b124adc5abcaf2fb7a6ab93bc14e563cfe882a7c3f83d41b06788d63"},
      {"value": "fascium_krea2_nsfwmerge4_fp8", "label": "FASCIUM Krea 2 Realistic Merge NSFW (FP8)", "checkpoint_id": "cp1_49df8aa87089ad0d21ae897cba2e2d1b838215b920c07cd5bdbbb6a5ff87c0e5"},
      {"value": "krea2_finalcut_update_fp8", "label": "Krea2 FinalCut Update (FP8)", "checkpoint_id": "cp1_e88cf4f82b5f1440b39d18b067d61a4def7ffa2867ead605d1125494f6c8504f"},
      {"value": "asian_utopian_krea2_v20_nvfp4", "label": "1125 Asian Utopian Krea 2 V2.0 (NVFP4)", "checkpoint_id": "cp1_89a932608d24f46a0737c10bd19c41ddfa524a65ca0e0585b3469c529b45605a"},
      {"value": "sat_ior_krea2_v2_fp8", "label": "SAT IOR Imitation of Reality V3 (FP8)", "checkpoint_id": "cp1_9a2149273d222d4b446ec762d2626d440895b4222648cfcaa43b1b0288e8f07a"},
      {"value": "sinox_krea2_edit_v11_fp8", "label": "SinoX Krea 2 Aesthetics Vision V1.1 (FP8)", "checkpoint_id": "cp1_5ee73c7e59c5006f7730bb959a9d539c2e2d9be7b6c8edd49bc016c97d53e3ac"},
      {"value": "dark_beast_3_krea2", "label": "Dark Beast 3 黑兽 3.0 (FP8)", "checkpoint_id": "cp1_f404d6b90594c8edd62135e1504872bb6af5085148b5903468a09568b2cb63e6"},
      {"value": "haruki_mix_kr2_v20", "label": "HARUKI MIX KR2 V2.0 (INT8 ConvRot)", "checkpoint_id": "cp1_fc4f7ebb33e554ddedc65f316e64c41ec521885243a39f29008945f3a3193b0c"},
      {"value": "redgpt2_krea2_turbo_gpt", "label": "REDGPT2 Krea 2 Turbo GPT逼真版 (FP8)", "checkpoint_id": "cp1_8a6ba80e526a7034bff04e772ca051a9b2599c665acfd81b6ef71e95d3657697"},
      {"value": "unstable_bastard_krea2", "label": "UnstableBastard BastardKrea2 (INT8)", "checkpoint_id": "cp1_b7a8577a4c28a014d9236918289c9c1148da60f71997471a63c740a5eb1c0cd9"},
      {"value": "ray_artshoot_krea2_v4", "label": "Ray Artshoot Krea 2 NSFW V4 (INT8)", "checkpoint_id": "cp1_366eec952a00fbb8ef3f857a14a9c227be87e0d3fdd4b176430d4e300b51a6d9"},
      {"value": "pornmaster_krea2_turbo_v2", "label": "PornMaster 色情大師 Krea 2 Turbo V2 (FP8)", "checkpoint_id": "cp1_b1a128f284fa0983c7cc67fb43db681be66c81b7fe45dd1aeb770ac1f59ca62c"},
      {"value": "fineporn_v4", "label": "FinePorn V4 (FP8)", "checkpoint_id": "cp1_b61c89eaed05e25eab86d84be52822f27801f394666aabdec263ef637f6df889"},
      {"value": "analog_madness_krea2_v30", "label": "Analog Madness Krea 2 Turbo V3.0 (FP8)", "checkpoint_id": "cp1_dd8953b905f14397798ee33b72eea5455132bb60f36024aea5f263e452a83bfa"},
      {"value": "cielbleu_krea2_v1", "label": "CielBleu Krea 2 V1 (FP8)", "checkpoint_id": "cp1_b6200595e4a991f5ee63e90055ad32b0ae6c65a149d46b0c00ed884cbe5a83d6"},
      {"value": "winnougan_10_toes_v13", "label": "Winnougan 10 Toes V1.3 (FP8)", "checkpoint_id": "cp1_84aca28923f5daa05d861185c65f604061cf626ed6d848aa266bef3bd111b172"},
      {"value": "lox_utopic_world_v20", "label": "Lox's Utopic World V2.0 Quants (FP8)", "checkpoint_id": "cp1_aaef3baf9b9a790cbd64f622a82f34b09f5a381cc609877db22b40944e55283f"},
      {"value": "jib_mix_krea_v10", "label": "Jib Mix Krea 2 V4.0 Habanero (INT8)", "checkpoint_id": "cp1_cbf26549abe22b6bde6c49cbbf2cbba5ff69851b4d3cc2acfc57953d6c7c8d0d"},
      {"value": "badmilk_melancholy_v10", "label": "Badmilkmelancholy Krea 2 Turbo V1.0 (FP8)", "checkpoint_id": "cp1_58899dc863451a1b8ab309a4949a7da50d917ecf9116fa075c7b0820891edfbd"},
      {"value": "unstable_dissolution_krea2", "label": "unStable Dissolution Krea 2 Turbo (FP8)", "checkpoint_id": "cp1_963b10a40613e7e1b116e87a438b0c7bfd2f6ec20de26d02c12b21cb4f6cea02"}
    ]
    }
  ]
},
  {
  "source_key": "6caacd9e42847dd2ce8def60e1aba1e57667c2f954ebc4cb867f44e759328ffb",
  "display_name": "Moody Krea2 Simple v2",
  "available": true,
  "readiness": "ready",
  "cached": true,
  "revision": {
    "publication_id": "f1532078-27bf-4978-a9ea-26ba7386af75"
  },
  "generation_source": {
    "generation_type": "text_to_image",
    "dimension_policy": "explicit",
    "summary": "Generates images from prompt guidance using explicit dimensions and returns a declared final result.",
    "tags": [
      "color_matching",
      "detailer",
      "image_blending",
      "krea2",
      "prompt_guided",
      "text_to_image",
      "ultimate_sd_upscale",
      "ultralytics"
    ],
    "base_model": {
      "family": "krea2",
      "family_label": "Krea 2",
      "architecture": "krea2",
      "architecture_label": "Krea 2"
    }
  },
  "technical_inventory": {
    "node_counts": {
      "editable_root": 100,
      "subgraph_definitions": 0,
      "editable_subgraph_nodes": 0,
      "compiled_api": 67,
      "output_reachable": 54,
      "compiled_orphans": 13
    },
    "upscalers": [
      "4x-ClearRealityV1.pth"
    ],
    "samplers": 2
  },
  "model_selectors": [
    {
      "parameter_id": "checkpoint",
      "label": "Checkpoint",
      "description": "Base checkpoint (UNETLoader binding). Option ids are stable; bindings point at installed NVFP4/FP8 quantizations under the shared model library.",
      "default": "moody_krea2_mix_v70",
      "choices": [
      {"value": "moody_krea2_mix_v70", "label": "Moody Krea 2 Mix V8.0 (NVFP4)", "checkpoint_id": "cp1_a90aeadb16481494e7ae43f8e8ba9c479d96f33282194e36facd90f6c619991e"},
      {"value": "moody_krea2_mix_v7se", "label": "Moody Krea 2 Mix V7SE 反差萌妹TYJR Vol2 (NVFP4)", "checkpoint_id": "cp1_51d6df323642f42e2e7f320e3cd6cb7a74c940107a24ccf2ad43fa51750e2086"},
      {"value": "moody_amateur_mix_v4", "label": "Moody Amateur Mix V5 Prosumer Shots (NVFP4)", "checkpoint_id": "cp1_765c6e13120ca77445f78c271fa7294f82dbba35a6daab490f502f7d01028c6f"},
      {"value": "gonzalomo_krea2_v40_fp8", "label": "Gonzalomo Krea 2 v40 (FP8)", "checkpoint_id": "cp1_639081bf28305bfc3fb16a088fc40719604aa0bc57eac0141a10802aaec1c1f3"},
      {"value": "dasiwa_krea2_turbo_cutedisaster_v2_fp8", "label": "Dasiwa Krea 2 Turbo Raw – Dark Desire V3 UC (FP8)", "checkpoint_id": "cp1_b81b317f3a3fa1475d7d534cb4a0594dcc7a216bcd8f8ab0e1efb22e9641a9ad"},
      {"value": "realism_by_stable_yogi_v25_nvfp4", "label": "Realism by Stable Yogi V2.5 (NVFP4)", "checkpoint_id": "cp1_7f90215e0c03cfb9c6213dc9fd83d91be7783d7a8bf83a1cf414baedc265a585"},
      {"value": "realism_by_stable_yogi_v30_nvfp4", "label": "Realism by Stable Yogi V3.0 Extended (NVFP4)", "checkpoint_id": "cp1_07ea5edc3f144e193e982fd50e56fcb6cac9b81a28c382f20e5235ba7bbe3add"},
      {"value": "selfora_v21_night_fix_fp8", "label": "Selfora v2.1 Night Fix (FP8)", "checkpoint_id": "cp1_721459142aa39c6ced072557205538f3705ce2b069d5a68ac50915a9b9ea90c1"},
      {"value": "muse_by_stable_yogi_v35_nvfp4", "label": "Muse by Stable Yogi V3.5 (NVFP4)", "checkpoint_id": "cp1_b2e08281f41fbb7ed5b735a01e2426ddf26fbf9a9461587bddb61673f0a8c2d1"},
      {"value": "sat_dirtyrealism_krea2_v5_fp8", "label": "SAT DirtyRealism Krea 2 – Dirty Uncut (FP8)", "checkpoint_id": "cp1_d0a936b10d49b978a2a7485a51c505378493a733379c5d8362bdb06f62f9ca8c"},
      {"value": "intorealism_krea2_edit_v10_int8", "label": "IntoRealism Krea 2 V5.0 (INT8)", "checkpoint_id": "cp1_7b2fba79b124adc5abcaf2fb7a6ab93bc14e563cfe882a7c3f83d41b06788d63"},
      {"value": "fascium_krea2_nsfwmerge4_fp8", "label": "FASCIUM Krea 2 Realistic Merge NSFW (FP8)", "checkpoint_id": "cp1_49df8aa87089ad0d21ae897cba2e2d1b838215b920c07cd5bdbbb6a5ff87c0e5"},
      {"value": "krea2_finalcut_update_fp8", "label": "Krea2 FinalCut Update (FP8)", "checkpoint_id": "cp1_e88cf4f82b5f1440b39d18b067d61a4def7ffa2867ead605d1125494f6c8504f"},
      {"value": "asian_utopian_krea2_v20_nvfp4", "label": "1125 Asian Utopian Krea 2 V2.0 (NVFP4)", "checkpoint_id": "cp1_89a932608d24f46a0737c10bd19c41ddfa524a65ca0e0585b3469c529b45605a"},
      {"value": "sat_ior_krea2_v2_fp8", "label": "SAT IOR Imitation of Reality V3 (FP8)", "checkpoint_id": "cp1_9a2149273d222d4b446ec762d2626d440895b4222648cfcaa43b1b0288e8f07a"},
      {"value": "sinox_krea2_edit_v11_fp8", "label": "SinoX Krea 2 Aesthetics Vision V1.1 (FP8)", "checkpoint_id": "cp1_5ee73c7e59c5006f7730bb959a9d539c2e2d9be7b6c8edd49bc016c97d53e3ac"},
      {"value": "dark_beast_3_krea2", "label": "Dark Beast 3 黑兽 3.0 (FP8)", "checkpoint_id": "cp1_f404d6b90594c8edd62135e1504872bb6af5085148b5903468a09568b2cb63e6"},
      {"value": "haruki_mix_kr2_v20", "label": "HARUKI MIX KR2 V2.0 (INT8 ConvRot)", "checkpoint_id": "cp1_fc4f7ebb33e554ddedc65f316e64c41ec521885243a39f29008945f3a3193b0c"},
      {"value": "redgpt2_krea2_turbo_gpt", "label": "REDGPT2 Krea 2 Turbo GPT逼真版 (FP8)", "checkpoint_id": "cp1_8a6ba80e526a7034bff04e772ca051a9b2599c665acfd81b6ef71e95d3657697"},
      {"value": "unstable_bastard_krea2", "label": "UnstableBastard BastardKrea2 (INT8)", "checkpoint_id": "cp1_b7a8577a4c28a014d9236918289c9c1148da60f71997471a63c740a5eb1c0cd9"},
      {"value": "ray_artshoot_krea2_v4", "label": "Ray Artshoot Krea 2 NSFW V4 (INT8)", "checkpoint_id": "cp1_366eec952a00fbb8ef3f857a14a9c227be87e0d3fdd4b176430d4e300b51a6d9"},
      {"value": "pornmaster_krea2_turbo_v2", "label": "PornMaster 色情大師 Krea 2 Turbo V2 (FP8)", "checkpoint_id": "cp1_b1a128f284fa0983c7cc67fb43db681be66c81b7fe45dd1aeb770ac1f59ca62c"},
      {"value": "fineporn_v4", "label": "FinePorn V4 (FP8)", "checkpoint_id": "cp1_b61c89eaed05e25eab86d84be52822f27801f394666aabdec263ef637f6df889"},
      {"value": "analog_madness_krea2_v30", "label": "Analog Madness Krea 2 Turbo V3.0 (FP8)", "checkpoint_id": "cp1_dd8953b905f14397798ee33b72eea5455132bb60f36024aea5f263e452a83bfa"},
      {"value": "cielbleu_krea2_v1", "label": "CielBleu Krea 2 V1 (FP8)", "checkpoint_id": "cp1_b6200595e4a991f5ee63e90055ad32b0ae6c65a149d46b0c00ed884cbe5a83d6"},
      {"value": "winnougan_10_toes_v13", "label": "Winnougan 10 Toes V1.3 (FP8)", "checkpoint_id": "cp1_84aca28923f5daa05d861185c65f604061cf626ed6d848aa266bef3bd111b172"},
      {"value": "lox_utopic_world_v20", "label": "Lox's Utopic World V2.0 Quants (FP8)", "checkpoint_id": "cp1_aaef3baf9b9a790cbd64f622a82f34b09f5a381cc609877db22b40944e55283f"},
      {"value": "jib_mix_krea_v10", "label": "Jib Mix Krea 2 V4.0 Habanero (INT8)", "checkpoint_id": "cp1_cbf26549abe22b6bde6c49cbbf2cbba5ff69851b4d3cc2acfc57953d6c7c8d0d"},
      {"value": "badmilk_melancholy_v10", "label": "Badmilkmelancholy Krea 2 Turbo V1.0 (FP8)", "checkpoint_id": "cp1_58899dc863451a1b8ab309a4949a7da50d917ecf9116fa075c7b0820891edfbd"},
      {"value": "unstable_dissolution_krea2", "label": "unStable Dissolution Krea 2 Turbo (FP8)", "checkpoint_id": "cp1_963b10a40613e7e1b116e87a438b0c7bfd2f6ec20de26d02c12b21cb4f6cea02"}
    ]
    }
  ]
},
  {
  "source_key": "60f0cdf5d61d7bdcda7cd766669cb7b7c2108fac3a3588df91cd7f724183afc3",
  "display_name": "Moody Krea2 Minimal v1",
  "available": true,
  "readiness": "ready",
  "cached": true,
  "revision": {
    "publication_id": "7226a332-47df-45a5-a2c0-fdf5ecd2b38e"
  },
  "generation_source": {
    "generation_type": "text_to_image",
    "dimension_policy": "explicit",
    "summary": "Generates images from prompt guidance using explicit dimensions and returns a declared final result.",
    "tags": [
      "krea2",
      "prompt_guided",
      "text_to_image"
    ],
    "base_model": {
      "family": "krea2",
      "family_label": "Krea 2",
      "architecture": "krea2",
      "architecture_label": "Krea 2"
    }
  },
  "technical_inventory": {
    "node_counts": {
      "editable_root": 27,
      "subgraph_definitions": 0,
      "editable_subgraph_nodes": 0,
      "compiled_api": 19,
      "output_reachable": 17,
      "compiled_orphans": 2
    },
    "upscalers": [],
    "samplers": 1
  },
  "model_selectors": [
    {
      "parameter_id": "checkpoint",
      "label": "Checkpoint",
      "description": "Base checkpoint for the Krea2 pipeline. V7.0 is the Moody Krea 2 Mix; V7SE is the fan-art remix variant. NVFP4.",
      "default": "moody_krea2_mix_v70",
      "choices": [
      {"value": "moody_krea2_mix_v70", "label": "Moody Krea 2 Mix V8.0 (NVFP4)", "checkpoint_id": "cp1_a90aeadb16481494e7ae43f8e8ba9c479d96f33282194e36facd90f6c619991e"},
      {"value": "moody_krea2_mix_v7se", "label": "Moody Krea 2 Mix V7SE 反差萌妹TYJR Vol2 (NVFP4)", "checkpoint_id": "cp1_51d6df323642f42e2e7f320e3cd6cb7a74c940107a24ccf2ad43fa51750e2086"},
      {"value": "moody_amateur_mix_v4", "label": "Moody Amateur Mix V5 Prosumer Shots (NVFP4)", "checkpoint_id": "cp1_765c6e13120ca77445f78c271fa7294f82dbba35a6daab490f502f7d01028c6f"},
      {"value": "gonzalomo_krea2_v40_fp8", "label": "Gonzalomo Krea 2 v40 (FP8)", "checkpoint_id": "cp1_639081bf28305bfc3fb16a088fc40719604aa0bc57eac0141a10802aaec1c1f3"},
      {"value": "dasiwa_krea2_turbo_cutedisaster_v2_fp8", "label": "Dasiwa Krea 2 Turbo Raw – Dark Desire V3 UC (FP8)", "checkpoint_id": "cp1_b81b317f3a3fa1475d7d534cb4a0594dcc7a216bcd8f8ab0e1efb22e9641a9ad"},
      {"value": "realism_by_stable_yogi_v25_nvfp4", "label": "Realism by Stable Yogi V2.5 (NVFP4)", "checkpoint_id": "cp1_7f90215e0c03cfb9c6213dc9fd83d91be7783d7a8bf83a1cf414baedc265a585"},
      {"value": "realism_by_stable_yogi_v30_nvfp4", "label": "Realism by Stable Yogi V3.0 Extended (NVFP4)", "checkpoint_id": "cp1_07ea5edc3f144e193e982fd50e56fcb6cac9b81a28c382f20e5235ba7bbe3add"},
      {"value": "selfora_v21_night_fix_fp8", "label": "Selfora v2.1 Night Fix (FP8)", "checkpoint_id": "cp1_721459142aa39c6ced072557205538f3705ce2b069d5a68ac50915a9b9ea90c1"},
      {"value": "muse_by_stable_yogi_v35_nvfp4", "label": "Muse by Stable Yogi V3.5 (NVFP4)", "checkpoint_id": "cp1_b2e08281f41fbb7ed5b735a01e2426ddf26fbf9a9461587bddb61673f0a8c2d1"},
      {"value": "sat_dirtyrealism_krea2_v5_fp8", "label": "SAT DirtyRealism Krea 2 – Dirty Uncut (FP8)", "checkpoint_id": "cp1_d0a936b10d49b978a2a7485a51c505378493a733379c5d8362bdb06f62f9ca8c"},
      {"value": "intorealism_krea2_edit_v10_int8", "label": "IntoRealism Krea 2 V5.0 (INT8)", "checkpoint_id": "cp1_7b2fba79b124adc5abcaf2fb7a6ab93bc14e563cfe882a7c3f83d41b06788d63"},
      {"value": "fascium_krea2_nsfwmerge4_fp8", "label": "FASCIUM Krea 2 Realistic Merge NSFW (FP8)", "checkpoint_id": "cp1_49df8aa87089ad0d21ae897cba2e2d1b838215b920c07cd5bdbbb6a5ff87c0e5"},
      {"value": "krea2_finalcut_update_fp8", "label": "Krea2 FinalCut Update (FP8)", "checkpoint_id": "cp1_e88cf4f82b5f1440b39d18b067d61a4def7ffa2867ead605d1125494f6c8504f"},
      {"value": "asian_utopian_krea2_v20_nvfp4", "label": "1125 Asian Utopian Krea 2 V2.0 (NVFP4)", "checkpoint_id": "cp1_89a932608d24f46a0737c10bd19c41ddfa524a65ca0e0585b3469c529b45605a"},
      {"value": "sat_ior_krea2_v2_fp8", "label": "SAT IOR Imitation of Reality V3 (FP8)", "checkpoint_id": "cp1_9a2149273d222d4b446ec762d2626d440895b4222648cfcaa43b1b0288e8f07a"},
      {"value": "sinox_krea2_edit_v11_fp8", "label": "SinoX Krea 2 Aesthetics Vision V1.1 (FP8)", "checkpoint_id": "cp1_5ee73c7e59c5006f7730bb959a9d539c2e2d9be7b6c8edd49bc016c97d53e3ac"},
      {"value": "dark_beast_3_krea2", "label": "Dark Beast 3 黑兽 3.0 (FP8)", "checkpoint_id": "cp1_f404d6b90594c8edd62135e1504872bb6af5085148b5903468a09568b2cb63e6"},
      {"value": "haruki_mix_kr2_v20", "label": "HARUKI MIX KR2 V2.0 (INT8 ConvRot)", "checkpoint_id": "cp1_fc4f7ebb33e554ddedc65f316e64c41ec521885243a39f29008945f3a3193b0c"},
      {"value": "redgpt2_krea2_turbo_gpt", "label": "REDGPT2 Krea 2 Turbo GPT逼真版 (FP8)", "checkpoint_id": "cp1_8a6ba80e526a7034bff04e772ca051a9b2599c665acfd81b6ef71e95d3657697"},
      {"value": "unstable_bastard_krea2", "label": "UnstableBastard BastardKrea2 (INT8)", "checkpoint_id": "cp1_b7a8577a4c28a014d9236918289c9c1148da60f71997471a63c740a5eb1c0cd9"},
      {"value": "ray_artshoot_krea2_v4", "label": "Ray Artshoot Krea 2 NSFW V4 (INT8)", "checkpoint_id": "cp1_366eec952a00fbb8ef3f857a14a9c227be87e0d3fdd4b176430d4e300b51a6d9"},
      {"value": "pornmaster_krea2_turbo_v2", "label": "PornMaster 色情大師 Krea 2 Turbo V2 (FP8)", "checkpoint_id": "cp1_b1a128f284fa0983c7cc67fb43db681be66c81b7fe45dd1aeb770ac1f59ca62c"},
      {"value": "fineporn_v4", "label": "FinePorn V4 (FP8)", "checkpoint_id": "cp1_b61c89eaed05e25eab86d84be52822f27801f394666aabdec263ef637f6df889"},
      {"value": "analog_madness_krea2_v30", "label": "Analog Madness Krea 2 Turbo V3.0 (FP8)", "checkpoint_id": "cp1_dd8953b905f14397798ee33b72eea5455132bb60f36024aea5f263e452a83bfa"},
      {"value": "cielbleu_krea2_v1", "label": "CielBleu Krea 2 V1 (FP8)", "checkpoint_id": "cp1_b6200595e4a991f5ee63e90055ad32b0ae6c65a149d46b0c00ed884cbe5a83d6"},
      {"value": "winnougan_10_toes_v13", "label": "Winnougan 10 Toes V1.3 (FP8)", "checkpoint_id": "cp1_84aca28923f5daa05d861185c65f604061cf626ed6d848aa266bef3bd111b172"},
      {"value": "lox_utopic_world_v20", "label": "Lox's Utopic World V2.0 Quants (FP8)", "checkpoint_id": "cp1_aaef3baf9b9a790cbd64f622a82f34b09f5a381cc609877db22b40944e55283f"},
      {"value": "jib_mix_krea_v10", "label": "Jib Mix Krea 2 V4.0 Habanero (INT8)", "checkpoint_id": "cp1_cbf26549abe22b6bde6c49cbbf2cbba5ff69851b4d3cc2acfc57953d6c7c8d0d"},
      {"value": "badmilk_melancholy_v10", "label": "Badmilkmelancholy Krea 2 Turbo V1.0 (FP8)", "checkpoint_id": "cp1_58899dc863451a1b8ab309a4949a7da50d917ecf9116fa075c7b0820891edfbd"},
      {"value": "unstable_dissolution_krea2", "label": "unStable Dissolution Krea 2 Turbo (FP8)", "checkpoint_id": "cp1_963b10a40613e7e1b116e87a438b0c7bfd2f6ec20de26d02c12b21cb4f6cea02"}
    ]
    }
  ]
}
];
