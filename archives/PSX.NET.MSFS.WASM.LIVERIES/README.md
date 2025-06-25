
# A simple tutorial for how to convert existing repaints to work with PSX.NET.MSFS.WASM

I say tutorial because this directory is not a complete livery, you
will need to download some files.

In the example I'm making a KLM 747-400ER freighter livery based on
the FAIB model and a repaint from www.juergenbaumbusch.de.

## What you will need

- A copy of this directory

- The FAIB freighter basepack from https://fsxaibureau.com/manufacturing/boeing/boeing-747-400/

- The epaint you want to use (in this case https://www.juergenbaumbusch.de/?p=6571, I used the FSX version)

- The PSX.NET.MSFS.WASM model (of course you already have this, but we
  need to copy a file from it)

## Step 1: download FAIB7474F.zip and ai74fklm_fsx.zip

https://fsxaibureau.com/manufacturing/boeing/boeing-747-400/boeing-747-400f-basepack/

http://www.juergenbaumbusch.de/paints/KLM/ai74fklm_fsx.zip

## Step 2: get the model file from the FAIB package

Extract FSX/FAIB_B747-400F_GE/model.FGE/FAIB_B7474F_GE.MDL from FAIB7474F.zip and copy to PSX.NET.MSFS.LIVERIES\SimObjects\Airplanes\KLM Cargo 747-400ERF\model.FGE\FAIB_B7474F_GE.MDL

Note: the existing file is a placeholder, just overwrite it.

## Step 3: get the texture file from the downloaded repaint

Extract texture.KLM Cargo/FAIB_B7474F_ge_t.dds from ai74fklm_fsx.zip and copy to PSX.NET.MSFS.LIVERIES\SimObjects\Airplanes\KLM Cargo 747-400ERF\texture.KLM Cargo\FAIB_B7474F_ge_t.dds

Note: the existing file is a placeholder, just overwrite it.

## Step 4: (optional) edit the PSX thumbnail images

In PSX.NET.MSFS.LIVERIES\SimObjects\Airplanes\KLM Cargo 747-400ERF\texture.KLM Cargo\  there are two thumbnail JPG files. To make it easier to tell your PSX liveries apart in MSFS, you can edit those JPG files.

## Step 5: update layout.json

Copy MSFSLayoutGenerator.exe from PSX.NET.MSFS.WAS (you'll find this in your Community folder) to PSX.NET.MSFS.LIVERIES/MSFSLayoutGenerator.exe

Drag the empty layout.json file onto MSFSLayoutGenerator.exe. It should now be a json file containing all the files in PSX.NET.MSFS.LIVERIES

## Step 6: install your new livery into the sim

Copy PSX.NET.MSFS.LIVERIES into your MSFS Community folder and restart MSFS.

Now you should hopefully see another livery for the PSX.NET.MSFS.WASM addon.

