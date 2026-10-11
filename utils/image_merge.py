import os
import math
from pathlib import Path
from typing import List
from PIL import Image
from PIL import ImageDraw, ImageFont


def _split_ordered(items: List[str], groups: int) -> List[List[str]]:
    """Split ordered items into at most ``groups`` balanced consecutive groups."""
    groups = max(1, min(groups, len(items)))
    base, remainder = divmod(len(items), groups)
    result = []
    start = 0
    for index in range(groups):
        size = base + (1 if index < remainder else 0)
        result.append(items[start:start + size])
        start += size
    return result


def build_composite_images(
    image_paths: List[str],
    output_dir: str,
    composite_count: int = 5,
    columns: int = 4,
    page_width: int = 384,
    jpeg_quality: int = 75,
    gutter: int = 4,
) -> List[str]:
    """Compress ordered PDF screenshots into a small set of grid composites.

    Existing composites are reused when they are newer than every input page.
    No page labels are added, preserving the LVLM-Direct no-citation contract.
    """
    if not image_paths:
        raise ValueError("image_paths is empty")
    if composite_count < 1 or columns < 1 or page_width < 32:
        raise ValueError("invalid composite_count/columns/page_width")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    groups = _split_ordered(image_paths, composite_count)
    newest_input = max(Path(path).stat().st_mtime for path in image_paths)
    outputs: List[str] = []

    for group_index, group in enumerate(groups, 1):
        output = destination / f"document_{group_index:02d}.jpg"
        if output.is_file() and output.stat().st_mtime >= newest_input:
            outputs.append(str(output))
            continue

        resized = []
        try:
            for path in group:
                with Image.open(path) as source:
                    rgb = source.convert("RGB")
                    height = max(1, round(rgb.height * page_width / rgb.width))
                    resized.append(rgb.resize((page_width, height), Image.Resampling.LANCZOS))
            rows = math.ceil(len(resized) / columns)
            row_heights = [
                max((img.height for img in resized[row * columns:(row + 1) * columns]), default=1)
                for row in range(rows)
            ]
            canvas_width = columns * page_width + (columns - 1) * gutter
            canvas_height = sum(row_heights) + (rows - 1) * gutter
            canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
            y = 0
            for row, row_height in enumerate(row_heights):
                for column, image in enumerate(resized[row * columns:(row + 1) * columns]):
                    x = column * (page_width + gutter)
                    canvas.paste(image, (x, y))
                y += row_height + gutter
            canvas.save(output, "JPEG", quality=jpeg_quality, optimize=True)
            canvas.close()
        finally:
            for image in resized:
                image.close()
        outputs.append(str(output))
    return outputs


def add_page_id_watermarks(
    image_paths: List[str], output_dir: str,
) -> List[str]:
    """Return copies with a visible stable ``P<page>`` label burned into pixels."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    outputs = []
    for source_value in image_paths:
        source = Path(source_value)
        match = __import__("re").search(r"(\d+)", source.stem)
        page_id = f"P{int(match.group(1))}" if match else "P0"
        # Keep a numeric stem so every downstream page-ID extractor sees P<n>.
        output = destination / f"{source.stem}.jpg"
        if output.is_file() and output.stat().st_mtime >= source.stat().st_mtime:
            outputs.append(str(output))
            continue
        with Image.open(source) as raw:
            image = raw.convert("RGB")
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=max(18, min(image.width, image.height) // 25))
            bbox = draw.textbbox((0, 0), page_id, font=font, stroke_width=2)
            pad = 10
            box = (pad, pad, pad + bbox[2] - bbox[0] + 2 * pad, pad + bbox[3] - bbox[1] + 2 * pad)
            draw.rectangle(box, fill="white", outline="black", width=2)
            draw.text(
                (2 * pad, 2 * pad), page_id, fill="black", font=font,
                stroke_width=1, stroke_fill="white",
            )
            image.save(output, "JPEG", quality=90)
        outputs.append(str(output))
    return outputs

def merge_images(image_paths, output_path, max_size):
    """
    
    参数:
    image_paths (list): PNG图像文件路径列表
    output_path (str): 输出文件路径
    max_size (int): 最大文件大小
    
    返回:
    str: 输出文件路径
    """
    if not image_paths:
        raise ValueError("图像路径列表为空")
    
    # 读取所有图像并获取尺寸
    images = []
    sizes = []
    for path in image_paths:
        img = Image.open(path)
        sizes.append(img.size)
        images.append(img)
    
    n = len(sizes)
    
    # 检查单个图像是否超过最大尺寸限制（65500像素）
    max_single_dim = max(max(w, h) for w, h in sizes)
    if max_single_dim > 65500:
        raise ValueError(f"单个图像尺寸({max_single_dim}px)超过Pillow最大限制(65500px)")

    # 计算横向/纵向布局的总尺寸
    total_width_horizontal = sum(w for w, h in sizes)
    total_height_horizontal = max(h for w, h in sizes)
    
    total_width_vertical = max(w for w, h in sizes)
    total_height_vertical = sum(h for w, h in sizes)
    
    # 检查横向/纵向布局是否有效
    valid_layouts = []
    
    # 横向布局
    if total_width_horizontal <= 65500 and total_height_horizontal <= 65500:
        valid_layouts.append(("horizontal", total_width_horizontal, total_height_horizontal))
    
    # 纵向布局
    if total_width_vertical <= 65500 and total_height_vertical <= 65500:
        valid_layouts.append(("vertical", total_width_vertical, total_height_vertical))
    
    # 网格布局（智能计算，避免超过65500）
    if n > 1:
        best_grid = None
        min_grid_size = float('inf')
        
        # 限制行数范围（避免计算量过大）
        max_rows = min(n, 100)
        for rows in range(1, max_rows + 1):
            cols = (n + rows - 1) // rows  # 向上取整
            
            # 计算网格布局的尺寸
            max_widths = [0] * cols
            max_heights = [0] * rows
            for idx in range(n):
                r = idx // cols
                c = idx % cols
                w, h = sizes[idx]
                if w > max_widths[c]:
                    max_widths[c] = w
                if h > max_heights[r]:
                    max_heights[r] = h
            
            total_width = sum(max_widths)
            total_height = sum(max_heights)
            
            # 检查尺寸限制
            if total_width <= 65500 and total_height <= 65500:
                # 选择总尺寸最小的网格
                grid_size = total_width * total_height
                if grid_size < min_grid_size:
                    min_grid_size = grid_size
                    best_grid = (rows, cols, total_width, total_height)
        
        if best_grid:
            valid_layouts.append(("grid", best_grid[2], best_grid[3], best_grid[0], best_grid[1]))
    
    # 检查是否有有效布局
    if not valid_layouts:
        raise ValueError(f"所有布局都超过65500像素限制（最大尺寸限制65500x65500）")
    
    # 选择总尺寸最小的布局
    valid_layouts.sort(key=lambda x: x[1] * x[2])
    layout_type, total_width, total_height, *extra = valid_layouts[0]
    
    # 创建新图像（白色背景）
    merged = Image.new('RGB', (total_width, total_height), color=(255, 255, 255))
    
    # 根据布局类型合并图像
    if layout_type == "horizontal":
        x_offset = 0
        max_height = total_height
        for img, (w, h) in zip(images, sizes):
            # 居中对齐
            y_offset = (max_height - h) // 2
            merged.paste(img, (x_offset, y_offset))
            x_offset += w
    
    elif layout_type == "vertical":
        y_offset = 0
        max_width = total_width
        for img, (w, h) in zip(images, sizes):
            # 居中对齐
            x_offset = (max_width - w) // 2
            merged.paste(img, (x_offset, y_offset))
            y_offset += h
    
    elif layout_type == "grid":
        rows, cols = extra[0], extra[1]
        max_widths = [0] * cols
        max_heights = [0] * rows
        for idx in range(n):
            r = idx // cols
            c = idx % cols
            w, h = sizes[idx]
            if w > max_widths[c]:
                max_widths[c] = w
            if h > max_heights[r]:
                max_heights[r] = h
        
        x_offset = 0
        for c in range(cols):
            y_offset = 0
            for r in range(rows):
                idx = r * cols + c
                if idx >= n:
                    break
                w, h = sizes[idx]
                # 居中对齐
                x_pos = x_offset + (max_widths[c] - w) // 2
                y_pos = y_offset + (max_heights[r] - h) // 2
                merged.paste(images[idx], (x_pos, y_pos))
                y_offset += max_heights[r]
            x_offset += max_widths[c]
    
    # 调整JPEG质量直到满足大小限制
    quality = 95
    while quality >= 0:
        # 临时保存用于检查大小
        temp_path = "temp_merged.jpg"
        merged.save(temp_path, "JPEG", quality=quality)
        file_size = os.path.getsize(temp_path)
        
        if file_size <= max_size:
            # 保存最终结果
            merged.save(output_path, "JPEG", quality=quality)
            return output_path
        else:
            quality -= 5
            if quality < 0:
                break
    
    # 如果所有质量都超过大小限制
    raise ValueError(f"无法将图像合并到{max_size/1024/1024:.2f}MB以内（即使quality=0也超过）")



def split_list_into_5_ordered(image_paths):
    """
    将图像路径列表按原顺序均分为5个子列表（尽量均匀）。
    
    参数:
        image_paths (list): 图像路径列表
        
    返回:
        list[list]: 包含5个子列表的列表
    """
    n = len(image_paths)
    if n == 0:
        return [[] for _ in range(5)]

    # 每组的基础长度
    base = n // 5
    remainder = n % 5  # 多出的部分依次分配到前几组

    sublists = []
    start = 0
    for i in range(5):
        end = start + base + (1 if i < remainder else 0)
        sublists.append(image_paths[start:end])
        start = end

    return sublists

# 示例用法：
if __name__ == "__main__":
    img_paths = ["/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png","/home/gpu/dzy/M3-CaseRAG/experiment_multiHop/MMLongBench-Doc/documents/0b85477387a9d0cc33fca0f4becaa0e5/1.png"]
    try:
        output = merge_images(img_paths,"1.jpg",3879731)
        print(output)
    except Exception as e:
        print(f"错误: {str(e)}")
