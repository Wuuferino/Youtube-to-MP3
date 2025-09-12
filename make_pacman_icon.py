from PIL import Image, ImageDraw

def make_icon():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []
    for sz in sizes:
        img = Image.new('RGBA', (sz, sz), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        margin = sz * 0.08
        bbox = [margin, margin, sz - margin, sz - margin]
        start = 30
        end = 330
        draw.pieslice(bbox, start=start, end=end, fill=(255, 235, 59, 255))  # Yellow
        eye_r = max(1, int(sz * 0.07))
        eye_x = sz * 0.55
        eye_y = sz * 0.30
        draw.ellipse([eye_x - eye_r, eye_y - eye_r, eye_x + eye_r, eye_y + eye_r], fill=(33, 33, 33, 255))
        images.append(img)
    images[0].save('pacman.ico', sizes=[(s, s) for s in sizes])
    print('pacman.ico generated')

if __name__ == '__main__':
    make_icon()
