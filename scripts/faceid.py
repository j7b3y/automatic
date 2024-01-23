import os
import cv2
import torch
import numpy as np
import gradio as gr
import diffusers
import huggingface_hub as hf
from PIL import Image
from modules import scripts, processing, shared, devices, images


MODELS = {
    'FaceID Base': 'h94/IP-Adapter-FaceID/ip-adapter-faceid_sd15.bin',
    'FaceID Plus': 'h94/IP-Adapter-FaceID/ip-adapter-faceid-plus_sd15.bin',
    'FaceID Plus v2': 'h94/IP-Adapter-FaceID/ip-adapter-faceid-plusv2_sd15.bin',
    'FaceID XL': 'h94/IP-Adapter-FaceID/ip-adapter-faceid_sdxl.bin'
}
app = None
ip_model = None
ip_model_name = None
ip_model_tokens = None
ip_model_rank = None
swapper = None


def dependencies():
    from installer import installed, install
    packages = [
        ('insightface', 'insightface'),
        ('git+https://github.com/tencent-ailab/IP-Adapter.git', 'ip_adapter'),
    ]
    for pkg in packages:
        if not installed(pkg[1], reload=False, quiet=True):
            install(pkg[0], pkg[1], ignore=True)


def face_id(p: processing.StableDiffusionProcessing, faces, image, model, override, tokens, rank, cache, scale, structure):
    global ip_model, ip_model_name, ip_model_tokens, ip_model_rank # pylint: disable=global-statement
    from insightface.utils import face_align
    from ip_adapter.ip_adapter_faceid import IPAdapterFaceID, IPAdapterFaceIDPlus, IPAdapterFaceIDXL

    face_embeds = torch.from_numpy(faces[0].normed_embedding).unsqueeze(0)
    face_image = face_align.norm_crop(image, landmark=faces[0].kps, image_size=224) # you can also segment the face

    ip_ckpt = MODELS[model]
    folder, filename = os.path.split(ip_ckpt)
    basename, _ext = os.path.splitext(filename)
    model_path = hf.hf_hub_download(repo_id=folder, filename=filename, cache_dir=shared.opts.diffusers_dir)
    if model_path is None:
        shared.log.error(f'FaceID download failed: model={model} file={ip_ckpt}')
        return None

    processing.process_init(p)
    if override:
        shared.sd_model.scheduler = diffusers.DDIMScheduler(
            num_train_timesteps=1000,
            beta_start=0.00085,
            beta_end=0.012,
            beta_schedule="scaled_linear",
            clip_sample=False,
            set_alpha_to_one=False,
            steps_offset=1,
        )
    shortcut = None
    if ip_model is None or ip_model_name != model or ip_model_tokens != tokens or ip_model_rank != rank or not cache:
        shared.log.debug(f'FaceID load: model={model} file={ip_ckpt} tokens={tokens} rank={rank}')
        if 'Plus' in model:
            image_encoder_path = "laion/CLIP-ViT-H-14-laion2B-s32B-b79K"
            ip_model = IPAdapterFaceIDPlus(
                sd_pipe=shared.sd_model,
                image_encoder_path=image_encoder_path,
                ip_ckpt=model_path,
                lora_rank=rank,
                num_tokens=tokens,
                device=devices.device,
                torch_dtype=devices.dtype,
            )
            shortcut = 'v2' in model
        elif 'XL' in model:
            ip_model = IPAdapterFaceIDXL(
                sd_pipe=shared.sd_model,
                ip_ckpt=model_path,
                lora_rank=rank,
                num_tokens=tokens,
                device=devices.device,
                torch_dtype=devices.dtype,
            )
        else:
            ip_model = IPAdapterFaceID(
                sd_pipe=shared.sd_model,
                ip_ckpt=model_path,
                lora_rank=rank,
                num_tokens=tokens,
                device=devices.device,
                torch_dtype=devices.dtype,
            )
        ip_model_name = model
        ip_model_tokens = tokens
        ip_model_rank = rank
    else:
        shared.log.debug(f'FaceID cached: model={model} file={ip_ckpt} tokens={tokens} rank={rank}')

    # main generate dict
    ip_model_dict = {
        'num_samples': p.batch_size,
        'width': p.width,
        'height': p.height,
        'num_inference_steps': p.steps,
        'scale': scale,
        'guidance_scale': p.cfg_scale,
        'faceid_embeds': face_embeds.shape,
    }

    # optional generate dict
    if shortcut is not None:
        ip_model_dict['shortcut'] = shortcut
    if 'Plus' in model:
        ip_model_dict['s_scale'] = structure
        ip_model_dict['face_image'] = face_image.shape
    shared.log.debug(f'FaceID args: {ip_model_dict}')
    if 'Plus' in model:
        ip_model_dict['face_image'] = face_image
    ip_model_dict['faceid_embeds'] = face_embeds

    # run generate
    processed_images = []
    ip_model.set_scale(scale)
    for i in range(p.n_iter):
        ip_model_dict.update(
            {
                'prompt': p.all_prompts[i],
                'negative_prompt': p.all_negative_prompts[i],
                'seed': int(p.all_seeds[i]),
            }
        )
        res = ip_model.generate(**ip_model_dict)
        if isinstance(res, list):
            processed_images += res
    ip_model.set_scale(0)

    if not cache:
        ip_model = None
        ip_model_name = None
    devices.torch_gc()

    p.extra_generation_params["IP Adapter"] = f'{basename}:{scale}'
    return processed_images


def face_swap(p: processing.StableDiffusionProcessing, image, source_face):
    import insightface.model_zoo
    global swapper # pylint: disable=global-statement
    if swapper is None:
        model_path = hf.hf_hub_download(repo_id='ezioruan/inswapper_128.onnx', filename='inswapper_128.onnx', cache_dir=shared.opts.diffusers_dir)
        router = insightface.model_zoo.model_zoo.ModelRouter(model_path)
        swapper = router.get_model()

    np_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    faces = app.get(np_image)

    res = np_image.copy()
    for target_face in faces:
        res = swapper.get(res, target_face, source_face, paste_back=True) # pylint: disable=too-many-function-args, unexpected-keyword-arg

    p.extra_generation_params["FaceSwap"] = f'{len(faces)}'
    np_image = cv2.cvtColor(res, cv2.COLOR_BGR2RGB)
    return Image.fromarray(np_image)


class Script(scripts.Script):
    def title(self):
        return 'FaceID'

    def show(self, is_img2img):
        return True if shared.backend == shared.Backend.DIFFUSERS else False

    # return signature is array of gradio components
    def ui(self, _is_img2img):
        with gr.Row():
            mode = gr.CheckboxGroup(label='Mode', choices=['FaceID', 'FaceSwap'], value=['FaceID'])
            model = gr.Dropdown(choices=list(MODELS), label='FaceID Model', value='FaceID Base')
        with gr.Row(visible=True):
            override = gr.Checkbox(label='Override sampler', value=True)
            cache = gr.Checkbox(label='Cache model', value=True)
        with gr.Row(visible=True):
            scale = gr.Slider(label='Strength', minimum=0.0, maximum=2.0, step=0.01, value=1.0)
            structure = gr.Slider(label='Structure', minimum=0.0, maximum=1.0, step=0.01, value=1.0)
        with gr.Row(visible=False):
            rank = gr.Slider(label='Rank', minimum=4, maximum=256, step=4, value=128)
            tokens = gr.Slider(label='Tokens', minimum=1, maximum=16, step=1, value=4)
        with gr.Row():
            image = gr.Image(image_mode='RGB', label='Image', source='upload', type='pil', width=512)
        return [mode, model, scale, image, override, rank, tokens, structure, cache]

    def run(self, p: processing.StableDiffusionProcessing, mode, model, scale, image, override, rank, tokens, structure, cache): # pylint: disable=arguments-differ, unused-argument
        if len(mode) == 0:
            return None
        dependencies()
        try:
            import onnxruntime
            from insightface.app import FaceAnalysis
        except Exception as e:
            shared.log.error(f'FaceID: {e}')
            return None
        if image is None:
            shared.log.error('FaceID: no init_images')
            return None
        if shared.sd_model_type != 'sd' and shared.sd_model_type != 'sdxl':
            shared.log.error('FaceID: base model not supported')
            return None

        global app # pylint: disable=global-statement
        if app is None:
            shared.log.debug(f"ONNX: device={onnxruntime.get_device()} providers={onnxruntime.get_available_providers()}")
            app = FaceAnalysis(name="buffalo_l", providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
            onnxruntime.set_default_logger_severity(3)
            app.prepare(ctx_id=0, det_thresh=0.5, det_size=(640, 640))

        if isinstance(image, str):
            from modules.api.api import decode_base64_to_image
            image = decode_base64_to_image(image).convert("RGB")

        np_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        faces = app.get(np_image)
        if len(faces) == 0:
            shared.log.error('FaceID: no faces found')
            return None
        for i, face in enumerate(faces):
            shared.log.debug(f'FaceID face: i={i+1} score={face.det_score:.2f} gender={"female" if face.gender==0 else "male"} age={face.age} bbox={face.bbox}')
            p.extra_generation_params[f"FaceID {i+1}"] = f'{face.det_score:.2f} {"female" if face.gender==0 else "male"} {face.age}y'

        processed_images = []
        if 'FaceID' in mode:
            processed_images = face_id(p, faces, np_image, model, override, tokens, rank, cache, scale, structure) # run faceid pipeline
            processed = processing.Processed(
                p,
                images_list=processed_images,
                seed=p.seed,
                subseed=p.subseed,
                index_of_first_image=0,
            )
            if 'FaceSwap' not in mode:
                if shared.opts.samples_save and not p.do_not_save_samples:
                    for i, image in enumerate(processed.images):
                        info = processing.create_infotext(p, index=i)
                        images.save_image(image, path=p.outpath_samples, seed=p.all_seeds[i], prompt=p.all_prompts[i], info=info, p=p)
            else:
                if shared.opts.save_images_before_face_restoration and not p.do_not_save_samples:
                    for i, image in enumerate(processed.images):
                        info = processing.create_infotext(p, index=i)
                        images.save_image(image, path=p.outpath_samples, seed=p.all_seeds[i], prompt=p.all_prompts[i], info=info, p=p, suffix="-before-face-swap")

        else:
            processed = processing.process_images(p) # run normal pipeline
            processed_images = processed.images

        if 'FaceSwap' in mode: # replace faces as postprocess
            processed.images = []
            for batch_image in processed_images:
                swapped_image = face_swap(p, batch_image, source_face=faces[0])
                processed.images.append(swapped_image)

            if shared.opts.samples_save and not p.do_not_save_samples:
                for i, image in enumerate(processed.images):
                    info = processing.create_infotext(p, index=i)
                    images.save_image(image, path=p.outpath_samples, seed=p.all_seeds[i], prompt=p.all_prompts[i], info=info, p=p)

        processed.info = processed.infotext(p, 0)
        processed.infotexts = [processed.info]

        return processed
