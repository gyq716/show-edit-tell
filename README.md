## Show, Edit and Tell: A Framework for Editing Image Captions
This contains the source code for *Show, Edit and Tell: A Framework for Editing Image Captions*

<p align="center">
  <img width="600" height="350" src="demo.png">
</p>

### Requirements
- Python 3.6 or 3.7
- PyTorch 1.0 or higher

For evaluation, you also need:
- Java 1.8.0
- [coco-api](https://github.com/cocodataset/cocoapi)
- [cococaption python 3](https://github.com/mtanti/coco-caption)


Argument Parser is currently not supported. We will add support to it soon. 

### Pretrained Models
You can download the pretrained models from [here](https://drive.google.com/drive/folders/1qyI8LD8p3qSVFC2hpVYULR8Rjr7hPBL-)

### Download Features
In this work, we use 36 fixed [bottom-up features](https://github.com/peteanderson80/bottom-up-attention). If you wish to use the adaptive features (10-100), please refer to `adaptive_features` folder in this repository and follow the instructions. 

First, download the fixed features from [here](https://imagecaption.blob.core.windows.net/imagecaption/trainval_36.zip) and unzip the file. Place the unzipped folder in `bottom-up_features` folder.  

Next type this command: 
```bash
python bottom-up_features/tsv.py
```

This command will create the following files:
<ul>
<li>An HDF5 file containing the bottom up image features for train and val splits, 36 per image for each split, in an (I, 36, 2048) tensor where I is the number of images in the split.</li>
<li>PKL files that contain training and validation image IDs mapping to index in HDF5 dataset created above.</li>
</ul>

### Download Caption Data
You can either download all the related caption data files from [here](https://drive.google.com/drive/folders/1JQx0M8fIUSdnXL-9i6-z3VF2bMBMP1LQ) or create them yourself.The folder contains the following:
-  `WORDMAP_coco`: maps the words to indices 
- `CAPUTIL`: stores the information about the existing captions in a dictionary organized as follows: `{"COCO_image_name": {"caption": "existing caption to be edited", "encoded_previous_caption": an encoded list of the words, "previous_caption_length": a list contaning the length of the caption, "image_ids": the COCO image id}`
- `CAPTIONS` the encoded ground-truth captions (a list with `number_images x 5` lists. Example: we have 113,287 training images in Karpathy Split, thereofre there is 566,435 lists for the training split)
- `CAPLENS`: the length of the ground-truth captions (a list with `number_images x 5` vallues)
- `NAMES`: the COCO image name in the same order as the CAPTIONS
- `GENOME_DETS`: the splits and image ids for loading the images in accordance to the features file created above

If you'd like to create the caption data yourself, download [Karpathy's Split](http://cs.stanford.edu/people/karpathy/deepimagesent/caption_datasets.zip) training, validation, and test splits. This zip file contains the captions. Place the file in `caption data` folder. You should also have the `pkl` files created from the 'Download Features' section: `train36_imgid2idx.pkl` and `val36_imgid2idx.pkl`.

Next, run: ```bash
python preprocess_caps.py
```


### Evaluation
All the generated captions and scores from our model can be found in the `outputs` folder. 

|                   | BLEU-1  | BLEU-4  |  CIDEr  | SPICE   |
|-------------------|:-------:|:-------:|:-------:|:-------:|
|Cross-Entropy Loss |  77.9   |  38.0   |  1.200  |  21.2   |
|CIDEr Optimization |  80.6   |  39.2   |  1.289  |  22.6   |

